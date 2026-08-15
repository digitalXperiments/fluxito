"""Data plane for hosted Streamlit apps.

The Streamlit process POSTs {alias, action, params} with the dashboard's
runtime token. Fluxito resolves the alias to a bound connection, builds a
refresh context for the dashboard owner, and dispatches the matching MCP
tool. Secrets never leave this process.
"""

from __future__ import annotations

import logging
from typing import Any

from app.dashboards import query_engine
from app.dashboards.artifact import CONNECTION_TOOL

logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_S = 25


def _binding_for(dash, alias: str) -> dict | None:
    for item in dash.connection_bindings or []:
        if isinstance(item, dict) and item.get("alias") == alias:
            return item
    return None


def _inject_bound_resource(binding: dict, params: dict) -> dict:
    """Overwrite resource identity from the binding. Caller cannot retarget it.

    Every populated host binding for ``resource_key``/``resource_value``,
    ``account_id``, and ``connection_id`` overwrites the caller value.
    Caller ``sql`` is rewritten to ``query`` only for warehouse tools that
    still expect that name — never used to pick another connection.
    """
    merged = dict(params)
    # Artifacts must not pick a tool or retarget the bound resource.
    merged.pop("tool", None)
    key = binding.get("resource_key")
    value = binding.get("resource_value")
    if key and value is not None and value != "":
        merged[key] = value
    if binding.get("account_id"):
        merged["account_id"] = binding["account_id"]
    if binding.get("connection_id"):
        merged["connection_id"] = binding["connection_id"]
    platform = binding.get("type")
    if platform in {"bigquery", "redshift", "snowflake"}:
        merged["engine"] = platform
        if "sql" in merged and "query" not in merged:
            merged["query"] = merged.pop("sql")
    return merged


async def run_alias_query(
    dash,
    *,
    alias: str,
    action: str,
    params: dict | None = None,
    tool: str | None = None,
    tool_manager: Any = None,
) -> dict:
    """Execute a live query for a hosted dashboard alias.

    ``tool`` is accepted only so leftover callers do not crash; it is ignored.
    Dispatch uses ``CONNECTION_TOOL[binding.type]`` only — never
    ``binding.tool``. Returns a JSON-serialisable dict. Never raises —
    errors are structured.
    """
    del tool  # caller-chosen tools are never honored
    binding = _binding_for(dash, alias)
    if binding is None:
        return {
            "error": True,
            "error_type": "unknown_alias",
            "message": (
                f"Alias {alias!r} is not in this dashboard's manifest. "
                "Add it to manifest.connections and redeploy."
            ),
        }
    if binding.get("status") == "missing":
        return {
            "error": True,
            "error_type": "unbound",
            "message": (
                f"No {binding.get('type')} connection is available in this project. "
                "Connect the platform in Fluxito, then reopen the dashboard."
            ),
        }
    if binding.get("status") == "error":
        return {
            "error": True,
            "error_type": "connection_error",
            "message": (
                f"The {binding.get('type')} connection bound to {alias!r} is not healthy. "
                "Reconnect it in Fluxito → Connections."
            ),
        }

    platform = binding.get("type") or ""
    tool_name = CONNECTION_TOOL.get(platform)
    if not tool_name:
        return {
            "error": True,
            "error_type": "no_tool",
            "message": f"No MCP tool is mapped for connection type {platform!r}.",
        }

    call_params = _inject_bound_resource(binding, params or {})
    spec = {
        "key": alias,
        "platform": platform,
        "tool": tool_name,
        "action": action,
        **call_params,
    }

    tm = tool_manager
    if tm is None:
        try:
            from app.main import mcp_server

            tm = mcp_server._tool_manager if mcp_server else None
        except Exception:
            tm = None
    if tm is None:
        return {
            "error": True,
            "error_type": "no_tool",
            "message": "MCP tool registry is not available.",
        }

    try:
        from app.auth.mcp_session_manager import build_refresh_context

        refresh_ctx = await build_refresh_context(str(dash.id))
    except Exception as exc:
        logger.warning("data_plane: refresh context failed for %s: %s", dash.id, exc)
        return {
            "error": True,
            "error_type": "refresh_context",
            "message": str(exc)[:300],
        }

    try:
        async with refresh_ctx:
            raw = await query_engine.run_card(
                tm,
                spec,
                tool_name=tool_name,
                action=action,
                timeout=_QUERY_TIMEOUT_S,
            )
    except ValueError as exc:
        return {"error": True, "error_type": "tool_not_registered", "message": str(exc)}
    except TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Query exceeded {_QUERY_TIMEOUT_S}s.",
        }
    except Exception as exc:
        logger.warning("data_plane: query failed alias=%s dash=%s: %s", alias, dash.id, exc)
        return {"error": True, "error_type": "query_failed", "message": str(exc)[:300]}

    if not isinstance(raw, dict):
        return {"data": raw, "alias": alias, "platform": platform}
    if raw.get("error") or raw.get("card_type") == "ERROR":
        return {
            "error": True,
            "error_type": raw.get("error_type", "tool_error"),
            "message": raw.get("message") or str(raw.get("error") or "tool error"),
            "alias": alias,
            "platform": platform,
        }
    raw = dict(raw)
    raw.setdefault("alias", alias)
    raw.setdefault("platform", platform)
    return raw
