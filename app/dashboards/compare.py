"""Date-range comparison: merge a current + comparison snapshot into one.

Compare mode executes each card twice (current range + comparison range) and this
module merges the two normalized snapshots into a single payload the templates
render. Pure functions — no DB, no I/O — so the delta math and row alignment are
unit-tested in isolation.

Rendering per card type (the template reads these augmented fields):
  scorecard -> each metric gains ``previous`` / ``delta_pct`` / ``delta_abs``
  table     -> each numeric cell gains ``<col>__prev`` and ``<col>__delta_pct``
  line/bar/area/stacked_bar/hbar/combo ->
               ``compare_series`` maps each numeric col -> previous values,
               aligned by relative index (day 1 vs day 1) — an overlay makes
               sense for any series-over-a-shared-axis chart family.
  pie/donut/scatter/heatmap/funnel/treemap/radar/gauge/waterfall/list/audit ->
               no overlay comparison (explicit no-op) — these either have no
               shared axis to align previous-period values against (pie,
               donut, scatter, heatmap, funnel, treemap, radar), show a single
               point-in-time value already covered by scorecard-style deltas
               (gauge), or aren't chart types at all (list, audit).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta


def pct_delta(cur: object, prev: object) -> float | None:
    """Percent change from prev to cur, rounded to 1 dp. None if undefined."""
    c, p = _to_num(cur), _to_num(prev)
    if c is None or p is None or p == 0:
        return None
    return round((c - p) / p * 100, 1)


def _to_num(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _shift_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year - 1, day=28)


def previous_range(start: str, end: str, mode: str) -> tuple[str, str]:
    """Return (prev_start, prev_end) ISO dates for a comparison ``mode``.

    previous_period — the immediately preceding window of equal length.
    previous_year   — the same dates one year earlier.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if mode == "previous_year":
        return _shift_year(s).isoformat(), _shift_year(e).isoformat()
    length = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def _numeric_cols(columns: list, rows: list) -> list:
    sample = rows[:5]
    out = []
    for c in columns:
        vals = [_to_num(r.get(c)) for r in sample]
        if vals and all(v is not None for v in vals):
            out.append(c)
    return out


def _label_col(columns: list, rows: list, numeric: list) -> str | None:
    for c in columns:
        if c not in numeric:
            return c
    return columns[0] if columns else None


def merge_compare(current: dict | None, previous: dict | None, chart_type: str | None) -> dict:
    """Return a copy of ``current`` augmented with comparison data from ``previous``."""
    out = copy.deepcopy(current or {})
    prev = previous or {}
    ct = (chart_type or "").lower()
    if ct == "scorecard":
        _merge_scorecard(out, prev)
    elif ct == "table":
        _merge_table(out, prev)
    elif ct in ("line", "bar", "area", "stacked_bar", "hbar", "combo"):
        _merge_series(out, prev)
    # pie / donut / scatter / heatmap / funnel / treemap / radar / gauge /
    # waterfall / list / audit: comparison overlay is not meaningful — no-op.
    # (out is returned unaugmented for these; templates fall back to
    # rendering the current snap alone, same as pie does today.)
    out["compare"] = True
    return out


def _merge_scorecard(out: dict, prev: dict) -> None:
    pmetrics = {m.get("key"): m for m in (prev.get("metrics") or [])}
    for m in out.get("metrics") or []:
        pm = pmetrics.get(m.get("key"))
        if not pm:
            continue
        cur, pv = _to_num(m.get("value")), _to_num(pm.get("value"))
        m["previous"] = pm.get("value")
        m["delta_pct"] = pct_delta(cur, pv)
        if cur is not None and pv is not None:
            m["delta_abs"] = round(cur - pv, 4)


def _merge_table(out: dict, prev: dict) -> None:
    cols = out.get("columns") or []
    rows = out.get("rows") or []
    numeric = _numeric_cols(cols, rows)
    label_col = _label_col(cols, rows, numeric)
    plookup: dict[str, dict] = {}
    if label_col:
        for pr in prev.get("rows") or []:
            plookup[str(pr.get(label_col))] = pr
    for r in rows:
        pr = plookup.get(str(r.get(label_col))) if label_col else None
        for c in numeric:
            pv = _to_num(pr.get(c)) if pr else None
            cv = _to_num(r.get(c))
            r[c + "__prev"] = pr.get(c) if pr else None
            r[c + "__delta_pct"] = pct_delta(cv, pv)
    out["compare_columns"] = numeric


def _merge_series(out: dict, prev: dict) -> None:
    cols = out.get("columns") or []
    rows = out.get("rows") or []
    numeric = _numeric_cols(cols, rows)
    label_col = _label_col(cols, rows, numeric)
    prows = list(prev.get("rows") or [])
    # Sort previous chronologically so index i aligns with current's index i
    # (the chart also sorts current ascending by its date label).
    if label_col:
        prows.sort(key=lambda r: str(r.get(label_col)))
    out["compare_series"] = {c: [_to_num(pr.get(c)) for pr in prows] for c in numeric}
    out["compare_columns"] = numeric
