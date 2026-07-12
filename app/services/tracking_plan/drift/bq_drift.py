# app/services/tracking_plan/drift/bq_drift.py
"""Tier 2 drift: per-parameter fill-rate + unplanned params from the GA4 BigQuery export.

Only reachable when the project has a BigQuery connection and a resolved GA4-export
dataset. GA4's Data API cannot enumerate arbitrary event parameters, so this is the
only path to fill-rate and unplanned-parameter detection.

``build_param_sql`` composes read-only SQL over the ``events_*`` sharded tables;
``parse_param_rows`` turns the result rows into observation records. Both are pure
so they can be unit-tested without BigQuery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# GA4 event names are snake_case identifiers. We inline them into SQL (run_query
# takes a bare string, no bind params), so anything outside this charset is
# rejected rather than escaped — defence in depth against injection.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{1,120}$")


@dataclass
class ParamObsRow:
    """Observed presence of one parameter on one live event."""

    event_name: str
    param_key: str
    present_pct: float | None
    sample_value: str | None
    is_unplanned: bool
    source: str = "bq"


def _suffix_window(days: int, *, now: datetime | None = None) -> tuple[str, str]:
    end = (now or datetime.now(UTC)).date()
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def build_param_sql(
    gcp_project: str,
    dataset: str,
    event_names: list[str],
    days: int = 7,
    *,
    now: datetime | None = None,
) -> str | None:
    """Compose the per-(event, param) presence query, or None if nothing safe to query.

    Restricts to ``event_names`` (the plan's events) so we can both measure planned
    params' fill-rate and discover unplanned keys on those events.
    """
    safe_names = [n for n in event_names if _SAFE_NAME.match(n)]
    if not safe_names or not _SAFE_NAME.match(dataset.replace("-", "_")):
        return None
    in_list = ", ".join(f"'{n}'" for n in safe_names)
    start, end = _suffix_window(days, now=now)
    table = f"`{gcp_project}.{dataset}.events_*`"
    return f"""
WITH totals AS (
  SELECT event_name, COUNT(*) AS total
  FROM {table}
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND event_name IN ({in_list})
  GROUP BY event_name
),
params AS (
  SELECT event_name, ep.key AS param_key, COUNT(*) AS present,
    ANY_VALUE(COALESCE(
      ep.value.string_value,
      CAST(ep.value.int_value AS STRING),
      CAST(ep.value.double_value AS STRING),
      CAST(ep.value.float_value AS STRING)
    )) AS sample_value
  FROM {table}, UNNEST(event_params) AS ep
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND event_name IN ({in_list})
  GROUP BY event_name, param_key
)
SELECT p.event_name, p.param_key, p.present, t.total, p.sample_value
FROM params p
JOIN totals t USING (event_name)
ORDER BY p.event_name, p.param_key
""".strip()


# Noise params GA4 attaches to every event — never surfaced as plan params or drift.
_GA4_NOISE_PARAMS = frozenset(
    {
        "ga_session_id",
        "ga_session_number",
        "engagement_time_msec",
        "engaged_session_event",
        "session_engaged",
        "page_location",
        "page_referrer",
        "page_title",
        "entrances",
        "debug_mode",
    }
)


def parse_param_rows(
    rows: list[dict],
    plan_params_by_event: dict[str, set[str]],
) -> list[ParamObsRow]:
    """Turn BigQuery result rows into observation records.

    ``plan_params_by_event`` maps event name → set of planned parameter names; a
    live key not in that set (and not GA4 noise) is flagged ``is_unplanned``.
    """
    out: list[ParamObsRow] = []
    for r in rows:
        event_name = r.get("event_name")
        param_key = r.get("param_key")
        if not event_name or not param_key:
            continue
        if param_key in _GA4_NOISE_PARAMS:
            continue
        total = r.get("total") or 0
        present = r.get("present") or 0
        pct = round(present / total * 100, 2) if total else None
        planned = plan_params_by_event.get(event_name, set())
        out.append(
            ParamObsRow(
                event_name=event_name,
                param_key=param_key,
                present_pct=pct,
                sample_value=(str(r["sample_value"]) if r.get("sample_value") is not None else None),
                is_unplanned=param_key not in planned,
            )
        )
    return out


async def fetch_param_rows(
    bq_conn, gcp_project: str, dataset: str, event_names: list[str], days: int = 7
) -> list[dict]:
    """Run the param-presence query. Returns [] on missing connector, unsafe SQL, or query error."""
    import app.app_state as app_state

    connector = app_state.bq_connector
    if connector is None:
        return []
    sql = build_param_sql(gcp_project, dataset, event_names, days)
    if sql is None:
        return []
    try:
        result = await connector.run_query(
            bq_conn.service_account_encrypted, gcp_project, sql, max_results=5000
        )
    except Exception:
        return []
    if result.get("error"):
        return []
    return list(result.get("rows") or [])
