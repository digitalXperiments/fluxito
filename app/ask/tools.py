"""Read-only bridge between the harness and the in-process MCP tool manager."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app import app_state
from app.ask.providers.base import (
    ChoicesBlock,
    ContentBlock,
    StreamEvent,
    ToolSpec,
    blocks_to_json,
)
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

# Write tools the model may never execute from Ask. Hosted dashboard deploy
# happens over MCP (deploy_dashboard / bind_dashboard), not via confirm-action.
# Kept as an empty gate so confirm-action cannot be pointed at card writers.
CONFIRM_GATED_TOOLS: frozenset[str] = frozenset()

# Ask-side virtual tools: NOT MCP tools. The harness intercepts these by name
# before any MCP dispatch (see dispatch_virtual_tool below).
# propose_card is retired: intercepted so it hard-errors instead of previewing.
VIRTUAL_TOOL_NAMES: frozenset[str] = frozenset({"propose_card", "ask_choices"})


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


async def run_mcp_tool(
    *, user_id: str, project_id: str, name: str, params: dict[str, Any]
) -> tuple[Any, bool, str]:
    """Run any registered MCP tool in-process under Ask Fluxito's RBAC context.

    This is the single RBAC-checked + audited dispatch path ('ask_fluxito'
    source tagging) used both by ``AskToolBridge.dispatch`` (read-only tools,
    gated by ``is_allowed_call``) and by the confirm-action route + the
    ``propose_card`` virtual tool for tools NOT in ``READ_ONLY_TOOLS``
    (hosted ``deploy_dashboard`` / ``bind_dashboard`` are MCP tools, not Ask
    writers).

    RBAC: for any normally-registered tool this dispatches through
    ``tool_manager.call_tool`` — the SAME instrumented entry point
    ``_install_tool_hook`` (app.tools.registry) monkey-patches onto the
    ToolManager instance — rather than the tool's raw ``.run()``. That hook is
    where the per-project-role RBAC backstop (``_tool_permitted_for_call``),
    circuit breaker, and audit trail actually live; calling ``tool.run()``
    directly bypasses all of it silently. This matters most for
    ``CONFIRM_GATED_TOOLS``: without going through ``call_tool``, a project
    member whose role denies ``dashboards:write`` could still add/remove
    dashboard cards via confirm-action, since the tool body itself only checks
    row ownership, not the caller's role permissions.

    Returns ``(parsed_result, is_error, raw_json_str)``.
    """
    from app.main import mcp_server

    tm = mcp_server._tool_manager
    registered_tool = tm.get_tool(name)
    legacy_tool = None if registered_tool is not None else tm._legacy_tools.get(name)
    if registered_tool is None and legacy_tool is None:
        err = {"error": f"Tool '{name}' is not registered."}
        return err, True, json.dumps(err)

    t0 = time.monotonic()
    try:
        async with _AskToolContext(user_id, project_id):
            try:
                if registered_tool is not None:
                    # Routed through the RBAC-checked + circuit-breaker + audited
                    # instrumented entry point — see the docstring above.
                    raw = await asyncio.wait_for(tm.call_tool(name, dict(params)), timeout=_TOOL_TIMEOUT_S)
                else:
                    # Legacy-only tools (superseded by a unified dispatcher tool
                    # and no longer resolvable via tm.get_tool/call_tool) are never
                    # exposed to the model via AskToolBridge.tool_specs(), so this
                    # branch is unreachable in the confirm-action / propose_card /
                    # read-tool paths today — kept only as a defensive fallback.
                    raw = await asyncio.wait_for(legacy_tool.run(dict(params)), timeout=_TOOL_TIMEOUT_S)
                content = json.dumps(raw, default=str)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                await _audit(name, params, content, raw, "success", elapsed_ms)
                return raw, False, content
            except TimeoutError:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                err = {"error": f"Tool '{name}' timed out."}
                err_text = json.dumps(err)
                await _audit(name, params, err_text, None, "error", elapsed_ms)
                return err, True, err_text
            except Exception as exc:  # surface tool errors to the caller, don't crash the loop
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                err = {"error": f"{type(exc).__name__}: {exc}"}
                err_text = json.dumps(err)
                await _audit(name, params, err_text, None, "error", elapsed_ms)
                return err, True, err_text
    except Exception as ctx_exc:  # _AskToolContext setup failed
        err = {"error": f"{type(ctx_exc).__name__}: {ctx_exc}"}
        return err, True, json.dumps(err)


async def _audit(
    name: str, params: dict[str, Any], raw_text: str, parsed: Any, status: str, duration_ms: int
) -> None:
    try:
        from app.tools.registry import _write_audit_row

        await _write_audit_row(
            tool_name=name,
            arguments=params,
            raw_text=raw_text,
            parsed=parsed,
            status=status,
            source_client="ask_fluxito",
            duration_ms=duration_ms,
        )
    except Exception:
        pass


@dataclass
class VirtualToolResult:
    """Result of dispatching an Ask-side virtual tool (propose_card / ask_choices)."""

    content: str
    is_error: bool
    block: ContentBlock | None
    event: StreamEvent | None


def virtual_tool_specs() -> list[ToolSpec]:
    """ToolSpecs for the two Ask-side virtual tools, in the same shape the bridge

    produces for real MCP tools — so the provider adapters treat them identically
    to any other callable tool.
    """
    return [
        ToolSpec(
            name="propose_card",
            description=(
                "RETIRED. Native card JSON is not how Fluxito dashboards are built. "
                "Do not call this. Author a production HTML/JS/CSS build (compile "
                "JSX/TSX locally, include every chart/font/image/chunk asset, and list "
                "every uploaded path in manifest.artifact_files), then call "
                "get_dashboard_authoring_guide → list_dashboard_connections → "
                "validate_dashboard_artifact → deploy_dashboard → bind_dashboard over MCP."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                },
                "required": [],
            },
        ),
        ToolSpec(
            name="ask_choices",
            description=(
                "Ask the user a structured multiple-choice question (e.g. which chart type, metric, "
                "or dimension to use) and show it as tappable chips in the chat. The user can also "
                "always reply with free text instead of tapping a chip. Use at most 6 options. After "
                "calling this, stop and wait for the user's reply — do not call any other tool in the "
                "same turn."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["label"],
                        },
                    },
                    "multi": {
                        "type": "boolean",
                        "description": "Whether the user may pick more than one option.",
                    },
                },
                "required": ["question", "options"],
            },
        ),
    ]


async def dispatch_virtual_tool(
    *, user_id: str, project_id: str, name: str, params: dict[str, Any]
) -> VirtualToolResult:
    """Intercept + handle an Ask-side virtual tool call. Never reaches MCP dispatch."""
    if name == "propose_card":
        return await _propose_card(user_id=user_id, project_id=project_id, params=params)
    if name == "ask_choices":
        return _ask_choices(params=params)
    raise ValueError(f"Not a virtual tool: {name}")


async def _propose_card(*, user_id: str, project_id: str, params: dict[str, Any]) -> VirtualToolResult:
    del user_id, project_id, params
    msg = (
        "propose_card is retired. Native card JSON is not deployed. "
        "Call get_dashboard_authoring_guide, list_dashboard_connections, build a "
        "complete production HTML/JS/CSS app using get_dashboard_query_recipe, list every uploaded "
        "path in manifest.artifact_files, validate_dashboard_artifact, deploy_dashboard, then "
        "bind_dashboard. Fluxito does not compile JSX/TSX or supply chart libraries."
    )
    return VirtualToolResult(
        content=json.dumps({"error": msg, "error_type": "hosted_only"}),
        is_error=True,
        block=None,
        event=None,
    )


def _ask_choices(*, params: dict[str, Any]) -> VirtualToolResult:
    question = str(params.get("question") or "").strip()
    raw_options = params.get("options") or []
    if not question or not raw_options:
        msg = "ask_choices requires a non-empty question and at least one option."
        return VirtualToolResult(content=json.dumps({"error": msg}), is_error=True, block=None, event=None)

    options: list[dict[str, str]] = []
    for o in raw_options[:6]:
        if isinstance(o, dict):
            label = str(o.get("label") or "").strip()
            value = str(o.get("value") or label)
        else:
            label = str(o)
            value = label
        if label:
            options.append({"label": label, "value": value})
    if not options:
        msg = "ask_choices requires at least one option with a label."
        return VirtualToolResult(content=json.dumps({"error": msg}), is_error=True, block=None, event=None)

    block = ChoicesBlock(
        id=f"ch_{uuid.uuid4().hex[:12]}",
        question=question,
        options=options,
        multi=bool(params.get("multi", False)),
    )
    event = StreamEvent(type="choices", block=blocks_to_json([block])[0])
    return VirtualToolResult(
        content="Options shown to the user; wait for their reply.", is_error=False, block=block, event=event
    )


class AskToolBridge:
    """Exposes the read-only tool surface (plus any section-unlocked write
    tools, and the Ask-side virtual tools) to the harness."""

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

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def project_id(self) -> str:
        return self._project_id

    def tool_specs(self) -> list[ToolSpec]:
        """Export normalized ToolSpecs for every allowlisted, registered tool
        the caller's RBAC permissions also allow, plus the two Ask-side
        virtual tools (propose_card, ask_choices)."""
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
        specs.extend(virtual_tool_specs())
        return specs

    async def dispatch(self, name: str, params: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool in-process. Returns (content_json_str, is_error).

        Virtual tools are never dispatched here — the harness intercepts them
        by name before calling this method.
        """
        if not is_allowed_call(name, params, self._section, self._eff):
            return (
                json.dumps({"error": f"Tool '{name}' is not permitted in this context."}),
                True,
            )
        _raw, is_error, content = await run_mcp_tool(
            user_id=self._user_id, project_id=self._project_id, name=name, params=params
        )
        return content, is_error


def new_tool_use_id() -> str:
    """A stable id for a tool_use block when a provider omits one (rare)."""
    return f"tu_{uuid.uuid4().hex[:12]}"
