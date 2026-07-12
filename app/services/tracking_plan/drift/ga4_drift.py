# app/services/tracking_plan/drift/ga4_drift.py
"""Tier 1 drift: diff plan events against live GA4 event volume.

The diff logic (``diff_events``) is a pure function over already-fetched data so
it is unit-testable without GA4 credentials. ``fetch_live_events`` is the thin
connector wrapper that supplies the ``live_events`` argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class EventDriftRow:
    """Computed drift for one event name (plan or live-only)."""

    event_name: str
    status: str  # verified | in_plan | drifted | broken | unplanned
    volume_7d: int | None = None
    reasons: list[str] = field(default_factory=list)
    source: str = "ga4"


def diff_events(
    plan_event_names: set[str],
    live_events: list[dict],
) -> dict[str, EventDriftRow]:
    """Reconcile plan event names against live GA4 volume.

    ``live_events`` is ``GA4Connector.list_events(...)["events"]`` — a list of
    ``{"event_name", "event_count"}``. Returns a dict keyed by event name.

    Rules:
      * plan event with 0 live volume        → ``broken``
      * plan event firing live               → ``verified`` (may be downgraded to
                                                ``drifted`` by the BigQuery tier)
      * live event name absent from the plan → ``unplanned``
    """
    live_by_name = {e["event_name"]: int(e.get("event_count") or 0) for e in live_events}
    rows: dict[str, EventDriftRow] = {}

    for name in plan_event_names:
        count = live_by_name.get(name, 0)
        if count <= 0:
            rows[name] = EventDriftRow(
                event_name=name,
                status="broken",
                volume_7d=0,
                reasons=["No live events received in the last 7 days."],
            )
        else:
            rows[name] = EventDriftRow(event_name=name, status="verified", volume_7d=count)

    for name, count in live_by_name.items():
        if name in plan_event_names:
            continue
        rows[name] = EventDriftRow(
            event_name=name,
            status="unplanned",
            volume_7d=count,
            reasons=["Firing live but not defined in the tracking plan."],
        )

    return rows


def window_dates(days: int = 7, *, now: datetime | None = None) -> tuple[str, str]:
    """Return (start, end) as GA4 ``YYYY-MM-DD`` strings for the last ``days``."""
    end = (now or datetime.now(UTC)).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


async def fetch_live_events(connection_id: str, property_id: str, days: int = 7) -> list[dict]:
    """Fetch live event volumes from GA4. Returns [] on any failure."""
    import app.app_state as app_state

    connector = app_state.ga4_connector
    if connector is None:
        return []
    start, end = window_dates(days)
    try:
        result = await connector.list_events(str(connection_id), property_id, start, end)
    except Exception:
        return []
    return list(result.get("events") or [])
