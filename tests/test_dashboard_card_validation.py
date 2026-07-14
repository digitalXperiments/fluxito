"""Tests for the card-native dashboard model and deploy tooling."""

from __future__ import annotations

import pytest

from app.models.dashboard import Dashboard, DashboardCard
from app.tools.dashboard_tools import _card_to_dict, _suggest_filters, _validate_card_specs

# ---------------------------------------------------------------------------
# Model field assertions
# ---------------------------------------------------------------------------


def test_dashboard_model_has_no_artifact_fields():
    """Dashboard must NOT carry legacy artifact/render-mode columns."""
    d = Dashboard()
    assert not hasattr(d, "artifact_js"), "artifact_js was removed"
    assert not hasattr(d, "artifact_html"), "artifact_html was removed"
    assert not hasattr(d, "render_mode"), "render_mode was removed"
    assert not hasattr(d, "insights"), "insights was removed"
    assert not hasattr(d, "deployed_by"), "deployed_by was removed"


def test_dashboard_card_has_chart_fields():
    """DashboardCard must have chart_type and chart_config columns."""
    c = DashboardCard()
    assert hasattr(c, "chart_type"), "DashboardCard must have chart_type"
    assert hasattr(c, "chart_config"), "DashboardCard must have chart_config"


def test_dashboard_card_no_artifact_fields():
    """DashboardCard must NOT have gcs_path or artifact_html."""
    c = DashboardCard()
    assert not hasattr(c, "gcs_path"), "gcs_path was removed"
    assert not hasattr(c, "artifact_html"), "artifact_html was removed"


# ---------------------------------------------------------------------------
# _validate_card_specs — happy path
# ---------------------------------------------------------------------------


def _valid_batch() -> list[dict]:
    return [
        {
            "key": "sessions_score",
            "title": "Sessions",
            "chart_type": "scorecard",
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "run_report",
            "params": {
                "property_id": "279951751",
                "metrics": ["sessions"],
                "dimensions": ["date"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        },
        {
            "key": "channel_bar",
            "title": "Traffic by Channel",
            "chart_type": "bar",
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "run_report",
            "params": {
                "property_id": "279951751",
                "metrics": ["sessions"],
                "dimensions": ["sessionDefaultChannelGroup"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        },
        {
            "key": "events_table",
            "title": "Events",
            "chart_type": "table",
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "run_report",
            "params": {
                "property_id": "279951751",
                "metrics": ["eventCount"],
                "dimensions": ["eventName"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        },
    ]


def test_deploy_batch_validates_valid_3_card_batch():
    """A well-formed 3-card batch passes _validate_card_specs without error."""
    result = _validate_card_specs(_valid_batch())
    assert len(result) == 3
    keys = {c["key"] for c in result}
    assert keys == {"sessions_score", "channel_bar", "events_table"}


def test_validate_card_specs_normalizes_filter_hooks():
    """filter_hooks defaults to {} when omitted."""
    result = _validate_card_specs(_valid_batch())
    for card in result:
        assert card["filter_hooks"] == {}


# ---------------------------------------------------------------------------
# _validate_card_specs — invalid chart_type
# ---------------------------------------------------------------------------


def test_deploy_batch_rejects_invalid_chart_type():
    cards = _valid_batch()
    # "heatmap" became a first-class chart_type in the dashboard revamp
    # (Phase 1) — use a type that is genuinely not in VALID_CHART_TYPES.
    cards[0]["chart_type"] = "not_a_real_chart_type"
    with pytest.raises(ValueError, match="chart_type 'not_a_real_chart_type' is not valid"):
        _validate_card_specs(cards)


def test_deploy_batch_rejects_empty_chart_type():
    cards = _valid_batch()
    cards[0]["chart_type"] = ""
    with pytest.raises(ValueError, match="chart_type must be a non-empty string"):
        _validate_card_specs(cards)


# ---------------------------------------------------------------------------
# _validate_card_specs — invalid platform
# ---------------------------------------------------------------------------


def test_deploy_batch_rejects_invalid_platform():
    cards = _valid_batch()
    cards[0]["platform"] = "fake_platform"
    # Unknown platform is a param_error (aggregated), so ValueError is raised
    with pytest.raises(ValueError, match="unknown platform"):
        _validate_card_specs(cards)


# ---------------------------------------------------------------------------
# _card_to_dict — key field
# ---------------------------------------------------------------------------


def test_card_to_dict_includes_key_from_query_params():
    """_card_to_dict must surface the 'key' stored inside query_params."""
    card = DashboardCard(
        title="Sessions",
        platform="ga4",
        tool_name="analytics_read",
        chart_type="scorecard",
        chart_config={"color_scheme": "blue"},
        query_params={"key": "sessions_score", "property_id": "279951751"},
        position=0,
    )
    d = _card_to_dict(card)
    assert "key" in d
    assert d["key"] == "sessions_score"


def test_card_to_dict_key_none_when_missing():
    """_card_to_dict returns key=None when query_params has no 'key'."""
    card = DashboardCard(
        title="Sessions",
        platform="ga4",
        tool_name="analytics_read",
        chart_type="scorecard",
        query_params={},
        position=0,
    )
    d = _card_to_dict(card)
    assert d["key"] is None


# ---------------------------------------------------------------------------
# _suggest_filters — infer dropdown filters from card dimensions
# ---------------------------------------------------------------------------


def test_suggest_filters_picks_up_known_dimensions():
    cards = [
        {"params": {"dimensions": ["date", "country", "deviceCategory"]}},
        {"params": {"dimensions": ["country"]}},  # dup country -> deduped
    ]
    out = _suggest_filters(cards)
    keys = {f["key"] for f in out}
    assert keys == {"country", "deviceCategory"}
    assert all(f["type"] == "single_select" for f in out)
    assert next(f for f in out if f["key"] == "country")["label"] == "Country"


def test_suggest_filters_ignores_unknown_and_missing_dims():
    cards = [
        {"params": {"dimensions": ["date", "someCustomDim"]}},
        {"params": {}},  # no dimensions
        {"params": {"dimensions": "not-a-list"}},
    ]
    assert _suggest_filters(cards) == []
