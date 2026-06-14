"""Tests for shared dashboard snapshot normalization.

Regression coverage for the "scorecard shows the date as 20.24M" bug: a GA4
scorecard returns a daily time series whose first column is the ``date``
dimension (YYYYMMDD). The renderer used to pick that as the metric value. The
normalizer now derives a proper ``metrics`` array from the metric headers.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.dashboards.hydration import card_to_payload
from app.dashboards.snapshot import card_type_from_chart_type, normalize_snap


def _ga4_daily_sessions() -> dict:
    """A GA4 run_report result: sessions by date, like a 'Total Sessions' card."""
    return {
        "dimension_headers": ["date"],
        "metric_headers": ["sessions"],
        "rows": [
            {"dimensions": ["20241001"], "metrics": ["100"]},
            {"dimensions": ["20241002"], "metrics": ["250"]},
            {"dimensions": ["20241003"], "metrics": ["150"]},
        ],
    }


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def test_flattens_ga4_nested_rows_into_named_columns():
    snap = normalize_snap(_ga4_daily_sessions(), chart_type="scorecard")
    assert snap["columns"] == ["date", "sessions"]
    assert snap["rows"][0] == {"date": "20241001", "sessions": "100"}


def test_already_flat_snap_is_left_alone():
    flat = {"columns": ["country", "sessions"], "rows": [{"country": "US", "sessions": "5"}]}
    snap = normalize_snap(dict(flat), chart_type="table")
    assert snap["columns"] == ["country", "sessions"]
    assert snap["rows"] == flat["rows"]


# ---------------------------------------------------------------------------
# Scorecard metric derivation — the core fix
# ---------------------------------------------------------------------------


def test_scorecard_value_is_the_metric_not_the_date():
    """The headline value must be the summed metric, NOT the date dimension."""
    snap = normalize_snap(_ga4_daily_sessions(), chart_type="scorecard")
    metrics = snap["metrics"]
    assert len(metrics) == 1
    m = metrics[0]
    assert m["key"] == "sessions"
    assert m["label"] == "Sessions"
    # 100 + 250 + 150 = 500 — and crucially NOT a 2024xxxx date.
    assert m["value"] == 500
    assert m["value"] < 20_000_000  # would be ~20.24M if it picked the date


def test_count_metric_is_summed_with_integer_display():
    snap = normalize_snap(_ga4_daily_sessions(), chart_type="scorecard")
    m = snap["metrics"][0]
    assert m["unit"] == "number"
    assert m["display"] == "500"


def test_rate_metric_is_averaged_and_shown_as_percent():
    raw = {
        "dimension_headers": ["date"],
        "metric_headers": ["engagementRate"],
        "rows": [
            {"dimensions": ["20241001"], "metrics": ["0.4"]},
            {"dimensions": ["20241002"], "metrics": ["0.6"]},
        ],
    }
    m = normalize_snap(raw, chart_type="scorecard")["metrics"][0]
    assert m["label"] == "Engagement Rate"
    assert m["unit"] == "percent"
    # average of 0.4 and 0.6 = 0.5 → 50%  (NOT summed to 1.0/100%)
    assert abs(m["value"] - 0.5) < 1e-9
    assert m["display"] == "50%"


def test_duration_metric_is_averaged_and_formatted():
    raw = {
        "dimension_headers": ["date"],
        "metric_headers": ["averageSessionDuration"],
        "rows": [
            {"dimensions": ["20241001"], "metrics": ["120"]},
            {"dimensions": ["20241002"], "metrics": ["240"]},
        ],
    }
    m = normalize_snap(raw, chart_type="scorecard")["metrics"][0]
    assert m["unit"] == "duration_sec"
    assert m["value"] == 180  # mean of 120 and 240 seconds
    assert m["display"] == "3m 0s"


def test_configured_unit_overrides_inference():
    raw = {
        "dimension_headers": ["date"],
        "metric_headers": ["revenue"],
        "rows": [
            {"dimensions": ["20241001"], "metrics": ["1000"]},
            {"dimensions": ["20241002"], "metrics": ["2500"]},
        ],
    }
    m = normalize_snap(raw, chart_type="scorecard", chart_config={"unit": "currency"})["metrics"][0]
    assert m["unit"] == "currency"
    assert m["value"] == 3500  # currency still sums
    assert m["display"] == "$3,500"


def test_flat_rows_without_headers_skip_date_columns():
    """Warehouse SQL returns flat rows with no metric_headers; a numeric date
    column must still be excluded from value candidates."""
    raw = {
        "columns": ["date", "sessions"],
        "rows": [
            {"date": "20241001", "sessions": "100"},
            {"date": "20241002", "sessions": "300"},
        ],
    }
    m = normalize_snap(raw, chart_type="scorecard")["metrics"][0]
    assert m["key"] == "sessions"
    assert m["value"] == 400


def test_multi_metric_scorecard_derives_all_metrics():
    raw = {
        "dimension_headers": ["date"],
        "metric_headers": ["sessions", "totalUsers"],
        "rows": [
            {"dimensions": ["20241001"], "metrics": ["100", "80"]},
            {"dimensions": ["20241002"], "metrics": ["200", "120"]},
        ],
    }
    metrics = normalize_snap(raw, chart_type="scorecard")["metrics"]
    assert [m["key"] for m in metrics] == ["sessions", "totalUsers"]
    assert metrics[0]["value"] == 300
    assert metrics[1]["label"] == "Total Users"
    assert metrics[1]["value"] == 200


def test_existing_metrics_are_not_overwritten():
    raw = {"metrics": [{"key": "x", "label": "X", "value": 7}], "card_type": "METRIC"}
    snap = normalize_snap(raw, chart_type="scorecard")
    assert snap["metrics"] == [{"key": "x", "label": "X", "value": 7}]


def test_non_scorecard_chart_type_gets_no_metrics():
    snap = normalize_snap(_ga4_daily_sessions(), chart_type="line")
    assert "metrics" not in snap
    assert snap["columns"] == ["date", "sessions"]  # still flattened


def test_empty_rows_produce_no_metrics():
    raw = {"dimension_headers": ["date"], "metric_headers": ["sessions"], "rows": []}
    snap = normalize_snap(raw, chart_type="scorecard")
    assert "metrics" not in snap


# ---------------------------------------------------------------------------
# chart_type → card_type mapping (PDF / Slack dispatch)
# ---------------------------------------------------------------------------


def test_card_type_from_chart_type():
    assert card_type_from_chart_type("scorecard") == "METRIC"
    assert card_type_from_chart_type("table") == "TABLE"
    assert card_type_from_chart_type("bar") == "CHART"
    assert card_type_from_chart_type("audit") == "AUDIT"
    assert card_type_from_chart_type(None) == "UNKNOWN"
    assert card_type_from_chart_type("nonsense") == "UNKNOWN"


# ---------------------------------------------------------------------------
# card_to_payload — PDF / Slack path end to end
# ---------------------------------------------------------------------------


def _fake_card(**overrides):
    base = {
        "id": "card-1",
        "title": "Total Sessions (2024)",
        "platform": "ga4",
        "chart_type": "scorecard",
        "chart_config": {},
        "result_cache": {},
        "refreshed_at": None,
        "_is_live": True,
        "_live_result": _ga4_daily_sessions(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_card_to_payload_renders_ga4_scorecard_as_metric():
    """The PDF/Slack path: a GA4 scorecard (no card_type, raw rows) must come
    out as a METRIC card with a derived metrics array — not 'UNKNOWN'."""
    payload = card_to_payload(_fake_card())
    assert payload["card_type"] == "METRIC"
    metrics = payload["snap"]["metrics"]
    assert metrics[0]["key"] == "sessions"
    assert metrics[0]["value"] == 500
