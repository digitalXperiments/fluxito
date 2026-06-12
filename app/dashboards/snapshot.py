"""
Shared dashboard snapshot normalization.

A "snap" is the raw result a card's tool returns. Different surfaces (the live
web view, PDF export, Slack/email scheduled reports) all need that raw result
massaged into the flat shape their renderers expect. This module is the single
source of truth for that transform so the surfaces never drift.

Two jobs:

1. **Flatten** the nested GA4 ``run_report`` shape into named ``columns`` + flat
   ``rows`` (other connectors already return flat rows — they pass through).

2. **Derive a ``metrics`` array for scorecards.** A scorecard shows ONE headline
   number, but a GA4 scorecard is required to carry a ``dimensions`` param
   (usually ``date``), so it comes back as a *daily time series*, not a single
   value. We collapse that series into one value per metric:

     * count / currency metrics  → SUM   (e.g. "Total Sessions (2024)" = year total)
     * rate / percent / duration → AVERAGE (summing daily rates is meaningless)

   Without this, the frontend's row-based fallback used to pick the *first
   numeric column* as the value — which for GA4 is the ``date`` dimension
   ("20241003"), rendered as "20.24M". See ``card_renderer_js.html``.
"""

from __future__ import annotations

import re
from typing import Any

# Dimension columns that *look* numeric (YYYYMMDD dates, month/year/hour numbers)
# but must never be treated as a metric value. GA4 tells us the real metrics via
# ``metric_headers``; this name-based check is only the fallback for flat-row
# sources (warehouse SQL etc.) that don't carry headers.
_DATE_COL_RE = re.compile(
    r"^(date|day|report_date|week|week_start|week_end|period|timestamp|datetime|"
    r"created_at|updated_at|year|month|year_?month|date_?hour(_?minute)?|"
    r"hour|minute|day_?of_?week|nth_?(day|week|month))$",
    re.IGNORECASE,
)
# Metrics whose daily values must be AVERAGED, not summed.
_RATE_COL_RE = re.compile(r"(rate|ratio|percent|pct|share|^ctr$|ctr$|^cvr$|cvr$)", re.IGNORECASE)
_DURATION_COL_RE = re.compile(
    r"(duration|time_?on|time_?spent|dwell_?time|avg_?time|session_?duration)", re.IGNORECASE
)

# chart_type → the base card_type the PDF / Slack renderers dispatch on.
_CHART_TYPE_TO_CARD_TYPE = {
    "scorecard": "METRIC",
    "table": "TABLE",
    "audit": "AUDIT",
    "list": "LIST",
    "bar": "CHART",
    "line": "CHART",
    "pie": "CHART",
}


def card_type_from_chart_type(chart_type: str | None) -> str:
    """Map a card's ``chart_type`` to the base ``card_type`` that the PDF and
    Slack renderers dispatch on. Unknown types fall back to ``"UNKNOWN"``."""
    return _CHART_TYPE_TO_CARD_TYPE.get((chart_type or "").lower(), "UNKNOWN")


def _to_number(value: Any) -> float | None:
    """Coerce a cell to a float, tolerating thousands separators and strings."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _humanize(col: str) -> str:
    """``totalUsers`` → ``Total Users``; ``engagement_rate`` → ``Engagement Rate``."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(col)).replace("_", " ")
    words = [w for w in spaced.split() if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) if words else str(col)


def _infer_unit(col: str) -> str:
    """Guess a display unit from the metric column name when none is configured."""
    if _DURATION_COL_RE.search(col):
        return "duration_sec"
    if _RATE_COL_RE.search(col):
        return "percent"
    return "number"


def _is_avg_metric(col: str, unit: str) -> bool:
    """True when a metric's daily values should be averaged rather than summed."""
    u = (unit or "").lower()
    if u in {
        "percent",
        "pct",
        "%",
        "duration",
        "duration_sec",
        "seconds",
        "sec",
        "duration_ms",
        "ms",
        "milliseconds",
    }:
        return True
    return bool(_RATE_COL_RE.search(col) or _DURATION_COL_RE.search(col))


def _format_display(n: float, unit: str) -> str:
    """Human-readable, unit-aware string for renderers that lack a formatter
    (the PDF only has ``fmt_number``; Slack only has ``_fmt``). The live web
    view formats client-side from ``value`` + ``unit`` and ignores this."""
    u = (unit or "").lower()
    if u in {"percent", "pct", "%"}:
        pct = n * 100 if 0 < abs(n) <= 1 else n
        return f"{round(pct, 1):g}%"
    if u in {"duration", "duration_sec", "seconds", "sec", "duration_ms", "ms", "milliseconds"}:
        secs = int(round(n / 1000 if u in {"duration_ms", "ms", "milliseconds"} else n))
        sign = "-" if secs < 0 else ""
        secs = abs(secs)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{sign}{h}h {m}m"
        if m:
            return f"{sign}{m}m {s}s"
        return f"{sign}{s}s"
    body = f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"
    if u in {"currency", "$"}:
        return f"${body}"
    return body


def _derive_scorecard_metrics(snap: dict, met_headers: list[str], chart_config: dict) -> dict:
    """Collapse a scorecard's rows into a ``metrics`` array (one entry per metric
    column, value aggregated across rows). No-op if the snap already has metrics
    or there's nothing numeric to aggregate."""
    rows = snap.get("rows") or []
    columns = snap.get("columns") or (list(rows[0].keys()) if rows and isinstance(rows[0], dict) else [])
    if not rows or not columns:
        return snap

    # Prefer GA4's authoritative metric headers; otherwise treat numeric
    # non-date columns as metrics (flat-row sources like warehouse SQL).
    if met_headers:
        metric_cols = [c for c in met_headers if c in columns]
    else:
        metric_cols = []
        for c in columns:
            if _DATE_COL_RE.match(str(c)):
                continue
            sample = [_to_number(r.get(c)) for r in rows[:5] if isinstance(r, dict)]
            if sample and all(v is not None for v in sample):
                metric_cols.append(c)
    if not metric_cols:
        return snap

    configured_unit = (chart_config.get("unit") or "").lower()
    metrics: list[dict] = []
    for col in metric_cols:
        nums = [v for v in (_to_number(r.get(col)) for r in rows if isinstance(r, dict)) if v is not None]
        if not nums:
            continue
        unit = configured_unit if configured_unit and configured_unit != "number" else _infer_unit(col)
        value = sum(nums) / len(nums) if _is_avg_metric(col, unit) else sum(nums)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        metrics.append(
            {
                "key": col,
                "label": _humanize(col),
                "value": value,
                "unit": unit or "number",
                "display": _format_display(value, unit),
            }
        )

    if metrics:
        snap = dict(snap)
        snap["metrics"] = metrics
    return snap


def normalize_snap(snap: Any, chart_type: str | None, chart_config: dict | None = None) -> Any:
    """Normalize a raw tool result into the flat format card renderers expect.

    GA4 ``run_report`` returns::

        {
          "dimension_headers": ["date"],
          "metric_headers": ["sessions"],
          "rows": [{"dimensions": ["20240101"], "metrics": ["1234"]}]
        }

    which becomes::

        {
          "columns": ["date", "sessions"],
          "rows": [{"date": "20240101", "sessions": "1234"}],
          "metrics": [{"key": "sessions", "label": "Sessions",
                       "value": <sum over rows>, "unit": "number",
                       "display": "1,234"}]   # scorecards only
        }

    Snaps that are already flat (have ``columns``) keep their rows; scorecards
    still get a ``metrics`` array derived from them.
    """
    if not isinstance(snap, dict):
        return snap

    dim_headers = snap.get("dimension_headers") or []
    met_headers = snap.get("metric_headers") or []
    raw_rows = snap.get("rows") or []

    # 1) Flatten nested GA4 rows into named columns (only when not already flat).
    if (dim_headers or met_headers) and "columns" not in snap and isinstance(raw_rows, list):
        columns = list(dim_headers) + list(met_headers)
        flat_rows: list = []
        for r in raw_rows:
            if isinstance(r, dict) and ("dimensions" in r or "metrics" in r):
                dims = r.get("dimensions") or []
                mets = r.get("metrics") or []
                row: dict = {}
                for i, col in enumerate(dim_headers):
                    row[col] = dims[i] if i < len(dims) else None
                for i, col in enumerate(met_headers):
                    row[col] = mets[i] if i < len(mets) else None
                flat_rows.append(row)
            else:
                flat_rows.append(r)
        snap = dict(snap)
        snap["columns"] = columns
        snap["rows"] = flat_rows

    # 2) Scorecards: collapse the (possibly multi-row) series into a metrics array.
    if (chart_type or "").lower() == "scorecard" and not snap.get("metrics"):
        snap = _derive_scorecard_metrics(snap, list(met_headers), chart_config or {})

    return snap
