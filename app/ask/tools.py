"""Read-only bridge between the harness and the in-process MCP tool manager."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from app import app_state
from app.ask.providers.base import ToolSpec
from app.auth.mcp_session_manager import build_project_context, build_user_context

# Public read tools the assistant may call.
#
# NOTE — Conversation approve flow (Ledger revamp Phase 1.1): the GTM diff
# card in the chat UI is driven by app.models.flux_draft.FluxDraft rows, not
# by anything this bridge dispatches — because this bridge is read-only.
# When a write-capable GTM tool (e.g. "tagmanager_write" action
# "propose_change") is added here, wire it to app.ask.drafts.DraftService.create
# + yield a "draft" StreamEvent from the harness. See the module docstring in
# app/ask/drafts.py for the exact call shape.
BASE_TOOLS: frozenset[str] = frozenset(
    {
        "analytics_read",
        "tagmanager_read",
        "marketing_read",
        "warehouse_read",
        "warehouse_query",
        "seo_read",
        "dashboard_read",
        "automation_read",
        "get_knowledge",
        "run_audit",
        "run_analysis",
        "get_session_context",
        "list_my_projects",
        "set_active_project",
        "tracking_plan",
    }
)

# Backward-compatible alias: the base surface is read-only.
READ_ONLY_TOOLS: frozenset[str] = BASE_TOOLS

# Extra tools unlocked when the chat is opened from a specific page section
# (page_context.section). These are *write-capable* tools whose scope is
# further constrained per-action below (see TAGMANAGER_WRITE_ASK_ACTIONS).
SECTION_TOOLS: dict[str, frozenset[str]] = {
    "implement": frozenset({"tagmanager_write"}),
}

# tracking_plan is a read/write dispatcher; only these actions are safe.
TRACKING_PLAN_READ_ACTIONS: frozenset[str] = frozenset(
    {
        "get_plan",
        "get_event",
        "get_overview",
        "validate",
        "list_rules",
        "list_branches",
        "get_branch",
        "diff",
        "list_comments",
        "export_markdown",
        "reconcile_preview",
        "list_dashboard_cards",
    }
)

# tagmanager_write is a write/publish dispatcher; the only action Ask Fluxito
# may ever invoke is propose_change, which stages a *draft* the user must
# approve before it touches the live container (see app.ask.drafts).
TAGMANAGER_WRITE_ASK_ACTIONS: frozenset[str] = frozenset({"propose_change"})

_TOOL_TIMEOUT_S = 60.0


# Representative params used only to probe whether a tool is permitted when
# listing the tool surface (tool_specs). tracking_plan is read-oriented in the
# ask context, so probe with a read action to keep it available to read-only
# members; tagmanager_write's write requirement is action-independent.
_SPEC_PROBE_PARAMS: dict[str, dict[str, Any]] = {
    "tracking_plan": {"action": "get_plan"},
    "tagmanager_write": {"action": "propose_change"},
}


def allowed_tools_for(section: str | None) -> frozenset[str]:
    """The full set of tool names permitted for a given page section."""
    return BASE_TOOLS | SECTION_TOOLS.get(section or "", frozenset())


def is_allowed_call(
    name: str,
    params: dict[str, Any],
    section: str | None = None,
    eff: Any = None,
) -> bool:
    """Server-side guard: is this tool+params a permitted call for this section?

    Two independent gates must both pass:
    1. The ask surface (section allowlist + per-action constraints) — this
       decides which tools the chat context exposes at all.
    2. The caller's RBAC ``EffectivePermissions`` — mirrors the check every
       other tool caller runs (``app.tools.registry._tool_permitted_for_call``)
       so a member cannot reach a write tool by merely asserting a section.
    """
    if name not in allowed_tools_for(section):
        return False
    if name == "tracking_plan":
        if params.get("action") not in TRACKING_PLAN_READ_ACTIONS:
            return False
    elif name == "tagmanager_write":
        if params.get("action") not in TAGMANAGER_WRITE_ASK_ACTIONS:
            return False
    # RBAC gate: when we know the caller's effective permissions, enforce them.
    if eff is not None:
        from app.auth.permissions import ALWAYS_ON_TOOLS

        if name not in ALWAYS_ON_TOOLS:
            action = params.get("action") if isinstance(params, dict) else None
            if not eff.allows_tool(name, action=action):
                return False
    return True


class _AskToolContext:
    """Set the MCP ContextVars for an in-process call inside an Ask Fluxito chat.

    Mirrors RefreshContext but tags the source as 'ask_fluxito' rather than
    'dashboard_refresh', and is built from explicit user/project ids.
    """

    __slots__ = ("_project_id", "_tokens", "_user_id")

    def __init__(self, user_id: str, project_id: str) -> None:
        self._user_id = user_id
        self._project_id = project_id
        self._tokens: list[tuple[str, Any]] = []

    async def __aenter__(self) -> _AskToolContext:
        user_ctx = await build_user_context(self._user_id)
        project_ctx = await build_project_context(self._project_id, self._user_id)
        self._tokens = [
            ("user", app_state.current_user_ctx.set(user_ctx)),
            ("project", app_state.current_project_ctx.set(project_ctx)),
            ("source", app_state.tool_call_source_ctx.set("ask_fluxito")),
            ("client", app_state.current_client_name_ctx.set("ask_fluxito")),
        ]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        vars = {
            "user": app_state.current_user_ctx,
            "project": app_state.current_project_ctx,
            "source": app_state.tool_call_source_ctx,
            "client": app_state.current_client_name_ctx,
        }
        for kind, token in reversed(self._tokens):
            try:
                vars[kind].reset(token)
            except ValueError:
                vars[kind].set(None)
        self._tokens = []


class AskToolBridge:
    """Exposes the read-only tool surface (plus any section-unlocked write
    tools) to the harness."""

    def __init__(
        self,
        user_id: str,
        project_id: str,
        section: str | None = None,
        eff: Any = None,
    ) -> None:
        self._user_id = user_id
        self._project_id = project_id
        self._section = section
        # Caller's resolved RBAC permissions. When set, both the exposed tool
        # surface and every dispatch are gated by it so a member cannot escalate
        # to a write tool by asserting a section they lack the role for.
        self._eff = eff

    @property
    def section(self) -> str | None:
        return self._section

    def tool_specs(self) -> list[ToolSpec]:
        """Export normalized ToolSpecs for every allowlisted, registered tool
        the caller's RBAC permissions also allow."""
        from app.main import mcp_server

        tm = mcp_server._tool_manager
        specs: list[ToolSpec] = []
        for name in sorted(allowed_tools_for(self._section)):
            tool = tm.get_tool(name)
            if tool is None:
                continue
            if not is_allowed_call(name, _SPEC_PROBE_PARAMS.get(name, {}), self._section, self._eff):
                continue
            specs.append(
                ToolSpec(
                    name=tool.name,
                    description=(tool.description or "").strip(),
                    input_schema=tool.parameters or {"type": "object", "properties": {}},
                )
            )
        return specs

    async def dispatch(self, name: str, params: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool in-process. Returns (content_json_str, is_error)."""
        if not is_allowed_call(name, params, self._section, self._eff):
            return (
                json.dumps({"error": f"Tool '{name}' is not permitted in this context."}),
                True,
            )
        from app.main import mcp_server

        tm = mcp_server._tool_manager
        tool = tm.get_tool(name) or tm._legacy_tools.get(name)
        if tool is None:
            return (json.dumps({"error": f"Tool '{name}' is not registered."}), True)
        t0 = time.monotonic()
        try:
            async with _AskToolContext(self._user_id, self._project_id):
                try:
                    raw = await asyncio.wait_for(tool.run(dict(params)), timeout=_TOOL_TIMEOUT_S)
                    content = json.dumps(raw, default=str)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    try:
                        from app.tools.registry import _write_audit_row

                        await _write_audit_row(
                            tool_name=name,
                            arguments=params,
                            raw_text=content,
                            parsed=raw,
                            status="success",
                            source_client="ask_fluxito",
                            duration_ms=elapsed_ms,
                        )
                    except Exception:
                        pass
                    return (content, False)
                except TimeoutError:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    err_text = json.dumps({"error": f"Tool '{name}' timed out."})
                    try:
                        from app.tools.registry import _write_audit_row

                        await _write_audit_row(
                            tool_name=name,
                            arguments=params,
                            raw_text=err_text,
                            parsed=None,
                            status="error",
                            source_client="ask_fluxito",
                            duration_ms=elapsed_ms,
                        )
                    except Exception:
                        pass
                    return (err_text, True)
                except Exception as exc:  # surface tool errors to the model, don't crash the loop
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    err_text = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                    try:
                        from app.tools.registry import _write_audit_row

                        await _write_audit_row(
                            tool_name=name,
                            arguments=params,
                            raw_text=err_text,
                            parsed=None,
                            status="error",
                            source_client="ask_fluxito",
                            duration_ms=elapsed_ms,
                        )
                    except Exception:
                        pass
                    return (err_text, True)
        except Exception as ctx_exc:  # _AskToolContext setup failed
            return (json.dumps({"error": f"{type(ctx_exc).__name__}: {ctx_exc}"}), True)


def new_tool_use_id() -> str:
    """A stable id for a tool_use block when a provider omits one (rare)."""
    return f"tu_{uuid.uuid4().hex[:12]}"
