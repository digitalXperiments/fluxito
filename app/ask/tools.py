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
READ_ONLY_TOOLS: frozenset[str] = frozenset(
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

_TOOL_TIMEOUT_S = 60.0


def is_allowed_call(name: str, params: dict[str, Any]) -> bool:
    """Server-side guard: is this tool+params a permitted read-only call?"""
    if name not in READ_ONLY_TOOLS:
        return False
    if name == "tracking_plan":
        action = params.get("action")
        return action in TRACKING_PLAN_READ_ACTIONS
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
    """Exposes the read-only tool surface to the harness."""

    def __init__(self, user_id: str, project_id: str) -> None:
        self._user_id = user_id
        self._project_id = project_id

    def tool_specs(self) -> list[ToolSpec]:
        """Export normalized ToolSpecs for every allowlisted, registered tool."""
        from app.main import mcp_server

        tm = mcp_server._tool_manager
        specs: list[ToolSpec] = []
        for name in sorted(READ_ONLY_TOOLS):
            tool = tm.get_tool(name)
            if tool is None:
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
        if not is_allowed_call(name, params):
            return (
                json.dumps({"error": f"Tool '{name}' is not permitted in read-only mode."}),
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
