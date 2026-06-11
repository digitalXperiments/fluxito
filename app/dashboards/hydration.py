"""
Shared Dashboard Card Hydration

Populates live results for dashboard cards (PDF, email, Slack).
Single source of truth for card execution + caching.

Consumers:
  1. pdf_renderer: Share PDF button + scheduled report exports
  2. scheduling.runner: Slack block rendering for scheduled reports

Behaviour:
  * Cards dispatch through the MCP tool registry via a synthetic refresh
    context (same path as /api/dashboard-query/{slug}/batch).
  * Cards whose tool is missing or that error fall back to result_cache.
  * Credentials: resolved via build_refresh_context (owner + project).

Public API:
  * hydrate_dashboard_cards(dashboard, cards, date_filter=None)
    — mutate cards in place, attach _live_result and _is_live
  * card_to_payload(card)
    — flatten hydrated card for PDF/Slack renderers
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.models.dashboard import Dashboard, DashboardCard

logger = logging.getLogger(__name__)

# Per-card upstream timeout so a hung query can't stall a PDF/email render.
_HYDRATE_CARD_TIMEOUT_S = 30


def _as_bool(value) -> bool:
    """Coerce a stored flag to bool — ``bool("false")`` is ``True``, so a
    string-persisted flag must be parsed explicitly."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def hydrate_dashboard_cards(
    dash: Dashboard,
    cards: list[DashboardCard],
    date_filter: dict[str, str] | None = None,
) -> None:
    """Populate ``_live_result`` and ``_is_live`` on each card in place.

    Dispatches each card through the MCP tool registry using a synthetic
    refresh context so that PDF, email, and Slack renderers can call this
    without an active MCP session.

    Args:
      dash         — the parent Dashboard (used to build the refresh context).
      cards        — the card rows to hydrate. May be a filtered subset.
      date_filter  — optional ``{"start_date": "YYYY-MM-DD",
                     "end_date": "YYYY-MM-DD"}`` injected as date overrides
                     into each card's params (respects date_locked flag).

    After this call, every card in ``cards`` has:
      * ``_live_result`` — dict with a ``card_type`` key and type-specific payload.
      * ``_is_live`` — True if the value came from a fresh execution,
        False if it fell back to cache or the execution errored.
    """
    from app.auth.mcp_session_manager import build_refresh_context
    from app.dashboards.filter_hooks import apply_overrides

    # Late import to avoid circular dep with app.main
    from app.main import mcp_server

    tm = mcp_server._tool_manager if mcp_server is not None else None

    refresh_ctx = await build_refresh_context(str(dash.id))

    async with refresh_ctx:
        tasks = [_hydrate_one_card(card, tm, date_filter, apply_overrides) for card in cards]
        await asyncio.gather(*tasks, return_exceptions=True)


async def _hydrate_one_card(
    card: DashboardCard,
    tm: Any,
    date_filter: dict[str, str] | None,
    apply_overrides,
) -> None:
    """Hydrate a single card in place. Never raises — falls back to cache on error."""
    spec = card.query_params or {}
    tool_name = spec.get("tool") or card.tool_name
    action = spec.get("action")

    if not tool_name or tm is None:
        card._live_result = card.result_cache or {}
        card._is_live = False
        return

    try:
        legacy = getattr(tm, "_legacy_tools", {})
        tool = legacy.get(tool_name) or tm._tools.get(tool_name)
        if tool is None:
            logger.warning("hydrate: tool '%s' not registered for card %s", tool_name, card.id)
            card._live_result = card.result_cache or {}
            card._is_live = False
            return

        # Merge date overrides (respecting date_locked flag on the card)
        card_date_locked = _as_bool(spec.get("date_locked"))
        overrides = date_filter if (date_filter and not card_date_locked) else None
        merged_spec = apply_overrides(spec, overrides)
        # Params are stored flattened in query_params — exclude spec metadata keys.
        # NOTE: "platform" is intentionally kept in call_args — it is a required
        # named parameter for analytics_read, marketing_read, etc.
        _META_KEYS = {"key", "tool", "filter_hooks", "filter_options", "date_locked"}
        call_args: dict = {k: v for k, v in merged_spec.items() if k not in _META_KEYS}
        if action is not None:
            call_args["action"] = action

        raw_result = await asyncio.wait_for(tool.run(call_args), timeout=_HYDRATE_CARD_TIMEOUT_S)
        if not isinstance(raw_result, dict):
            raw_result = {"card_type": "UNKNOWN", "raw": raw_result}

        if raw_result.get("card_type") == "ERROR" or raw_result.get("error"):
            # Surface the error but don't silently swap in stale cache
            card._live_result = raw_result
            card._is_live = False
        else:
            card._live_result = raw_result
            card._is_live = True
    except Exception as exc:
        logger.warning("hydrate: tool dispatch failed for card %s: %s", card.id, exc)
        card._live_result = card.result_cache or {}
        card._is_live = False


def card_to_payload(card: DashboardCard) -> dict[str, Any]:
    """Normalise a hydrated card to the shape downstream renderers expect.

    Both the PDF Jinja template and the Slack Block Kit renderer expect
    the same flat dict with: ``id``, ``title``, ``platform``,
    ``card_type``, ``is_live``, ``snap``, ``refreshed_at``. Keeping this
    shape centralised means one change propagates to all consumers.
    """
    snap = getattr(card, "_live_result", None) or card.result_cache or {}
    if not isinstance(snap, dict):
        snap = {"card_type": "UNKNOWN", "raw": snap}

    return {
        "id": str(card.id),
        "title": card.title,
        "platform": (card.platform or "").lower(),
        "card_type": snap.get("card_type", "UNKNOWN"),
        "is_live": bool(getattr(card, "_is_live", False)),
        "snap": snap,
        "refreshed_at": card.refreshed_at.isoformat() if card.refreshed_at else None,
    }
