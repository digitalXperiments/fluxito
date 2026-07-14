"""
Shared Card Query Dispatch Engine

Single source of truth for "given a dashboard card's stored query spec, run it
through the MCP tool registry" — the piece that used to be duplicated at three
call sites:

  1. app/api/dashboard_query_routes.py  (``batch_query`` — public per-card refresh)
  2. app/dashboards/hydration.py        (``hydrate_dashboard_cards`` — PDF/Slack/email)
  3. app/api/dashboard_routes.py        (``public_dashboard_json`` +
     ``live_dashboard_data`` — public share JSON + authenticated live view)

What's unified here (identical logic across all sites, safe to share):
  * ``META_KEYS`` exclusion + filter-override merge (``apply_overrides``)
  * ``date_locked`` flag coercion
  * MCP tool resolution (``_legacy_tools`` / ``_tools``)
  * timeout-bounded ``tool.run()`` dispatch
  * ``warehouse_query`` param renames (``engine=``, ``sql`` -> ``query``)
  * ``{placeholder}`` date substitution in warehouse SQL

What's intentionally NOT unified (differs per site, kept local to each caller):
  * Result normalization: ``dashboard_query_routes`` uses its own light
    ``_normalize_result`` (rows/columns only, no scorecard metric derivation);
    ``hydration`` and ``dashboard_routes`` use ``snapshot.normalize_snap``
    (adds scorecard metric derivation). Forcing these to converge would change
    the response shape of one of the three surfaces.
  * Caching tier + key: ``mmq:`` per-card 300s Redis cache (batch_query) vs
    ``dashdata:v1`` whole-response cache, 3600s (public) / dashboard-configured
    TTL (authenticated live view) vs no cache at all (hydration — the caller
    already sits behind a scheduled-report cache elsewhere).
  * Error/fallback strategy: ``batch_query`` returns a per-card error string
    to the browser; ``hydration`` silently falls back to ``result_cache`` and
    never raises; ``dashboard_routes`` falls back to ``result_cache`` AND (for
    the dashboard owner only) surfaces a structured ``live_error``.
  * Audit logging (``DashboardQueryLog``) — ``batch_query`` only.
  * Typed dashboard-filter application (``filter_translators.apply_card_filters``)
    and compare-mode re-execution (``compare.merge_compare``) — authenticated
    live view only.

Keep new shared logic here narrow and behavior-preserving; site-specific
control flow (which cache, whether to log, what the fallback looks like)
belongs in the call site, not this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.dashboards.filter_hooks import apply_overrides

# Spec keys that are metadata, not tool call parameters. "platform" is
# intentionally kept — it is a required named parameter for analytics_read,
# marketing_read, etc.
META_KEYS = frozenset({"key", "tool", "filter_hooks", "filter_options", "date_locked"})


def as_bool(value: Any) -> bool:
    """Coerce a stored flag to bool — ``bool("false")`` is ``True``, so a
    string-persisted flag must be parsed explicitly."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def is_date_locked(spec: dict) -> bool:
    """True if this card's stored spec pins its own date range and should
    ignore dashboard-level date overrides."""
    return as_bool(spec.get("date_locked"))


def resolve_tool(tm: Any, tool_name: str) -> Any | None:
    """Look up a registered MCP tool by name, checking the legacy registry
    first (most connectors are still registered there). Returns ``None`` if
    ``tm`` is unset or the tool isn't registered — callers decide the
    fallback."""
    if tm is None:
        return None
    legacy = getattr(tm, "_legacy_tools", {})
    return legacy.get(tool_name) or getattr(tm, "_tools", {}).get(tool_name)


def build_call_args(spec: dict, overrides: dict | None, action: str | None = None) -> dict[str, Any]:
    """Merge filter overrides into a card's stored spec and strip spec
    metadata keys, producing the flat kwargs dict a ``tool.run()`` expects.

    Params are stored flattened in ``query_params`` — spec metadata
    (``META_KEYS``) is excluded, but ``platform`` is intentionally kept.
    """
    merged = apply_overrides(spec, overrides)
    call_args: dict[str, Any] = {k: v for k, v in merged.items() if k not in META_KEYS}
    if action is not None:
        call_args["action"] = action
    return call_args


def apply_warehouse_renames(tool_name: str, platform: str, call_args: dict[str, Any]) -> bool:
    """In place: default ``engine`` to the card's platform and rename a
    legacy ``sql`` key to ``query`` for ``warehouse_query`` cards.

    Returns True if this is a warehouse card (callers use this to gate the
    ``{placeholder}`` substitution step below); no-op otherwise.
    """
    is_warehouse = tool_name == "warehouse_query"
    if is_warehouse:
        call_args.setdefault("engine", platform)
        if "sql" in call_args and "query" not in call_args:
            call_args["query"] = call_args.pop("sql")
    return is_warehouse


def substitute_date_placeholders(
    call_args: dict[str, Any], resolve_relative_date: Callable[[str], str]
) -> None:
    """In place: replace ``{param}`` tokens in a warehouse card's SQL with the
    other ``call_args`` values (resolving relative dates like ``today-7d``
    first). Targeted ``str.replace`` (not ``format_map``) so other
    curly-brace patterns in the SQL don't raise ``KeyError`` and silently
    swallow all substitutions. No-op if there's no ``query`` key."""
    if "query" not in call_args:
        return
    q = call_args["query"]
    for k, v in call_args.items():
        if k == "query" or not isinstance(v, str):
            continue
        resolved = resolve_relative_date(v)
        q = q.replace("{" + k + "}", resolved)
    call_args["query"] = q


async def dispatch(tool: Any, call_args: dict[str, Any], timeout: float) -> Any:
    """Run a resolved tool with a bounded timeout.

    Raises ``TimeoutError`` on expiry, or whatever the tool itself raises —
    callers own the fallback/error behavior.
    """
    return await asyncio.wait_for(tool.run(call_args), timeout=timeout)


async def run_card(
    tm: Any,
    spec: dict,
    *,
    tool_name: str | None = None,
    action: str | None = None,
    overrides: dict | None = None,
    timeout: float = 25,
) -> Any:
    """Resolve + dispatch a card's tool call in one step: merge overrides,
    strip metadata, then run with a timeout. Does **not** apply warehouse
    renames or ``{placeholder}`` substitution — callers that need those
    (``dashboard_routes``) call the granular helpers above directly, since
    the simpler call sites (``batch_query``, ``hydrate_dashboard_cards``)
    never exercised that path.

    Raises:
      ValueError   — no tool name given/in spec, or the tool isn't registered.
      TimeoutError — ``tool.run()`` exceeded ``timeout`` seconds.
      Exception    — whatever the tool itself raises.
    """
    resolved_tool_name = tool_name or spec.get("tool")
    if not resolved_tool_name:
        raise ValueError("card spec has no 'tool'")
    tool = resolve_tool(tm, resolved_tool_name)
    if tool is None:
        raise ValueError(f"Tool '{resolved_tool_name}' not registered")

    call_args = build_call_args(spec, overrides, action)
    return await dispatch(tool, call_args, timeout)
