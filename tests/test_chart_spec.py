"""Tests for the formal chart schema (app/dashboards/chart_spec.py).

No DB/Redis needed — chart_spec.py is pure Pydantic models, and
_validate_card_specs/VALID_CHART_TYPES import cleanly without app state.
"""

from __future__ import annotations

import pytest

from app.dashboards import snapshot
from app.dashboards.chart_spec import (
    CHART_TYPES,
    export_json_schema,
    parse_chart_spec,
    validate_chart_config,
)
from app.tools.dashboard_tools import VALID_CHART_TYPES, _validate_card_specs

# ---------------------------------------------------------------------------
# Drift guard — every VALID_CHART_TYPES member has a ChartSpec model and a
# _CHART_TYPE_TO_CARD_TYPE entry (the three vocabularies must move in lockstep,
# per the dashboard revamp plan).
# ---------------------------------------------------------------------------


def test_valid_chart_types_matches_chart_spec_models():
    assert VALID_CHART_TYPES == CHART_TYPES


def test_every_chart_type_has_a_card_type_mapping():
    for ct in VALID_CHART_TYPES:
        assert ct in snapshot._CHART_TYPE_TO_CARD_TYPE, f"{ct} missing from _CHART_TYPE_TO_CARD_TYPE"
        assert snapshot.card_type_from_chart_type(ct) != "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-type happy path — validate_chart_config accepts a sensible config for
# every chart_type in the vocabulary.
# ---------------------------------------------------------------------------

_HAPPY_CONFIGS: dict[str, dict] = {
    "scorecard": {"unit": "currency", "sparkline": True, "color_scheme": "green"},
    "bar": {"x": "date", "series": [{"col": "sessions", "label": "Sessions"}]},
    "line": {"x": "date", "series": [{"col": "sessions"}], "smooth": True, "highlight_last": True},
    "pie": {"series": [{"col": "revenue"}], "donut": False, "show_legend": True},
    "table": {"unit": "number"},
    "audit": {},
    "list": {},
    "area": {"x": "date", "series": [{"col": "sessions"}], "smooth": True},
    "combo": {
        "x": "date",
        "series": [
            {"col": "sessions", "kind": "bar"},
            {"col": "conversion_rate", "kind": "line", "axis": "right"},
        ],
    },
    "stacked_bar": {"x": "date", "series": [{"col": "a", "stack": "total"}, {"col": "b", "stack": "total"}]},
    "hbar": {"x": "channel", "series": [{"col": "spend"}], "orientation": "horizontal"},
    "donut": {"series": [{"col": "revenue"}], "donut": True},
    "scatter": {"x_col": "spend", "y_col": "conversions", "size_col": "impressions"},
    "heatmap": {"x_col": "hour", "y_col": "day_of_week", "value_col": "sessions"},
    "funnel": {"stage_col": "stage", "value_col": "users"},
    "treemap": {"label_col": "channel", "value_col": "revenue", "parent_col": "category"},
    "radar": {"label_col": "campaign", "value_cols": ["ctr", "cvr", "roas"]},
    "gauge": {"value_col": "score", "min": 0, "max": 100, "target": 80},
    "waterfall": {"label_col": "step", "delta_col": "delta"},
}


@pytest.mark.parametrize("chart_type", sorted(_HAPPY_CONFIGS))
def test_happy_path_per_chart_type(chart_type):
    normalized, warnings = validate_chart_config(chart_type, _HAPPY_CONFIGS[chart_type])
    assert warnings == []
    assert isinstance(normalized, dict)


def test_happy_configs_cover_every_chart_type():
    """Guard against the fixture drifting from the real vocabulary."""
    assert set(_HAPPY_CONFIGS) == CHART_TYPES


# ---------------------------------------------------------------------------
# Legacy-shape acceptance — today's 7 documented chart_type/chart_config
# combinations, including the pre-Phase-1 sub-mode shapes.
# ---------------------------------------------------------------------------


def test_legacy_bar_with_stacked_bar_submode():
    """chart_type='bar' + chart_config.type='stacked_bar' — pre-Phase-1 shape."""
    cfg = {
        "type": "stacked_bar",
        "x": "date",
        "series": [
            {"col": "organic", "stack": "total", "color": "primary"},
            {"col": "paid", "stack": "total", "color": "#ff8800"},
        ],
        "show_legend": True,
    }
    normalized, warnings = validate_chart_config("bar", cfg)
    assert warnings == []
    assert normalized["type"] == "stacked_bar"
    assert len(normalized["series"]) == 2


def test_legacy_bar_with_hbar_orientation():
    cfg = {"x": "country", "series": [{"col": "sessions"}], "orientation": "horizontal"}
    normalized, warnings = validate_chart_config("bar", cfg)
    assert warnings == []
    assert normalized["orientation"] == "horizontal"


def test_legacy_line_with_area_submode():
    cfg = {"type": "area", "x": "date", "series": [{"col": "revenue", "axis": "left"}]}
    normalized, warnings = validate_chart_config("line", cfg)
    assert warnings == []
    assert normalized["type"] == "area"


def test_legacy_pie_with_donut_flag():
    cfg = {"series": [{"col": "revenue"}], "donut": True}
    normalized, warnings = validate_chart_config("pie", cfg)
    assert warnings == []
    assert normalized["donut"] is True


def test_legacy_scorecard_config():
    cfg = {"unit": "duration", "sparkline": False, "color_scheme": "amber"}
    normalized, warnings = validate_chart_config("scorecard", cfg)
    assert warnings == []
    assert normalized["unit"] == "duration"


def test_legacy_table_and_audit_and_list_accept_empty_config():
    for ct in ("table", "audit", "list"):
        normalized, warnings = validate_chart_config(ct, {})
        assert warnings == []
        assert normalized == {}


def test_legacy_config_extra_keys_preserved_not_rejected():
    """Forward/legacy-compatible unknown keys must not fail validation."""
    cfg = {"series": [{"col": "sessions"}], "some_future_key": {"nested": True}}
    normalized, warnings = validate_chart_config("bar", cfg)
    assert warnings == []
    assert normalized.get("some_future_key") == {"nested": True}


def test_none_chart_config_is_accepted():
    normalized, warnings = validate_chart_config("scorecard", None)
    assert warnings == []
    assert normalized == {}


# ---------------------------------------------------------------------------
# Rejection of nonsense
# ---------------------------------------------------------------------------


def test_unknown_chart_type_via_tool_path_rejected():
    cards = [
        {
            "key": "bad",
            "title": "Bad Card",
            "chart_type": "sankey",  # not in VALID_CHART_TYPES
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "run_report",
            "params": {
                "property_id": "1",
                "metrics": ["sessions"],
                "dimensions": ["date"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        }
    ]
    with pytest.raises(ValueError, match="chart_type 'sankey' is not valid"):
        _validate_card_specs(cards)


def test_unknown_chart_type_via_validate_chart_config_is_a_warning_not_a_hard_error():
    """validate_chart_config alone (no VALID_CHART_TYPES gate) is permissive —
    the tool-level gate above is what actually rejects unknown chart_type."""
    normalized, warnings = validate_chart_config("sankey", {"foo": "bar"})
    assert warnings and "unknown chart_type" in warnings[0]
    assert normalized == {"foo": "bar"}


def test_wrong_typed_series_rejected():
    with pytest.raises(ValueError, match="chart_config invalid"):
        validate_chart_config("bar", {"series": "not-a-list"})


def test_series_entry_missing_col_rejected():
    with pytest.raises(ValueError, match="chart_config invalid"):
        validate_chart_config("bar", {"series": [{"label": "No column here"}]})


def test_chart_config_not_a_dict_rejected():
    with pytest.raises(ValueError, match="chart_config must be an object"):
        validate_chart_config("bar", "not-a-dict")  # type: ignore[arg-type]


def test_wrong_typed_series_rejected_via_tool_path():
    """_validate_card_specs aggregates chart_config errors into its fail-fast format."""
    cards = [
        {
            "key": "bad_series",
            "title": "Bad Series",
            "chart_type": "bar",
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "run_report",
            "chart_config": {"series": "not-a-list"},
            "params": {
                "property_id": "1",
                "metrics": ["sessions"],
                "dimensions": ["date"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        }
    ]
    with pytest.raises(ValueError, match="chart_config invalid"):
        _validate_card_specs(cards)


# ---------------------------------------------------------------------------
# export_json_schema / parse_chart_spec
# ---------------------------------------------------------------------------


def test_export_json_schema_covers_every_chart_type():
    schemas = export_json_schema()
    assert set(schemas) == CHART_TYPES
    for ct, schema in schemas.items():
        assert isinstance(schema, dict)
        assert "properties" in schema


def test_parse_chart_spec_discriminated_union_happy_path():
    spec = parse_chart_spec({"chart_type": "gauge", "chart_config": {"value_col": "score", "max": 100}})
    assert spec.chart_type == "gauge"
    assert spec.chart_config.value_col == "score"


def test_parse_chart_spec_rejects_unknown_chart_type():
    with pytest.raises(Exception):  # pydantic.ValidationError
        parse_chart_spec({"chart_type": "sankey", "chart_config": {}})
