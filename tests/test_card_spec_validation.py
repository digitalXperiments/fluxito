"""Tests for deploy-time card spec validation in dashboard_deploy_batch / dashboard_deploy_card."""

from __future__ import annotations

import pytest

from app.tools.dashboard_tools import (
    _CARD_PARAM_REQUIREMENTS,
    VALID_PLATFORMS,
    _validate_card_specs,
)


def _ga4_card(**overrides) -> dict:
    base = {
        "key": "kpi",
        "title": "Sessions KPI",
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
    }
    base.update(overrides)
    return base


def test_valid_ga4_card_passes():
    out = _validate_card_specs([_ga4_card()])
    assert len(out) == 1
    assert out[0]["key"] == "kpi"
    assert out[0]["filter_hooks"] == {}


def test_ga4_missing_metrics_fails():
    card = _ga4_card()
    card["params"].pop("metrics")
    with pytest.raises(ValueError, match="params.metrics is required"):
        _validate_card_specs([card])


def test_ga4_empty_metrics_list_fails():
    card = _ga4_card()
    card["params"]["metrics"] = []
    with pytest.raises(ValueError, match="params.metrics must be a non-empty list"):
        _validate_card_specs([card])


def test_ga4_missing_dimensions_fails():
    card = _ga4_card()
    card["params"].pop("dimensions")
    with pytest.raises(ValueError, match="params.dimensions is required"):
        _validate_card_specs([card])


def test_ga4_missing_both_lists_reports_all_errors():
    card = _ga4_card()
    card["params"].pop("metrics")
    card["params"].pop("dimensions")
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([card])
    msg = str(exc.value)
    assert "params.metrics is required" in msg
    assert "params.dimensions is required" in msg


def test_multiple_cards_aggregate_errors():
    bad1 = _ga4_card(key="a")
    bad1["params"].pop("metrics")
    bad2 = _ga4_card(key="b")
    bad2["params"].pop("dimensions")
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([bad1, bad2])
    msg = str(exc.value)
    # Both cards' errors surface at once
    assert "card 'a'" in msg and "params.metrics" in msg
    assert "card 'b'" in msg and "params.dimensions" in msg


def test_bigquery_requires_query():
    card = {
        "key": "bq",
        "title": "BigQuery Result",
        "chart_type": "table",
        "platform": "bigquery",
        "tool": "warehouse_query",
        "action": "run_query",
        "params": {},
    }
    with pytest.raises(ValueError, match="params.query is required"):
        _validate_card_specs([card])


def test_bigquery_connection_id_optional():
    # connection_id is auto-resolved — only query is strictly required
    card = {
        "key": "bq",
        "title": "BigQuery Result",
        "chart_type": "table",
        "platform": "bigquery",
        "tool": "warehouse_query",
        "action": "run_query",
        "params": {"query": "SELECT 1"},
    }
    out = _validate_card_specs([card])
    assert len(out) == 1


def test_meta_get_campaigns_requires_ad_account_id():
    card = {
        "key": "m",
        "title": "Meta Campaigns",
        "chart_type": "table",
        "platform": "meta_ads",  # the real card platform name (was "meta": invalid + dead key)
        "tool": "marketing_read",
        "action": "get_campaigns",
        "params": {},
    }
    with pytest.raises(ValueError, match="params.ad_account_id is required"):
        _validate_card_specs([card])


def test_unknown_action_is_not_validated():
    """Actions not in the _CARD_PARAM_REQUIREMENTS registry pass through — the
    tool's own runtime validation catches problems at refresh time."""
    card = {
        "key": "custom",
        "title": "Custom Card",
        "chart_type": "table",
        "platform": "ga4",
        "tool": "analytics_read",
        "action": "some_future_action_not_in_registry",
        "params": {},
    }
    out = _validate_card_specs([card])
    assert len(out) == 1


def test_action_none_is_rejected_for_action_based_tool():
    # Regression: a card with action=None is dispatched as analytics_read(action=None)
    # at refresh, which the tool rejects ("action: Input should be a valid string").
    # The deploy must reject it up front with an actionable message, not store a
    # silently-broken card.
    card = {
        "key": "c",
        "title": "GA4 Card",
        "chart_type": "scorecard",
        "platform": "ga4",
        "tool": "analytics_read",
        "action": None,
        "params": {},
    }
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([card])
    msg = str(exc.value)
    assert "action" in msg and "required" in msg
    assert "run_report" in msg  # suggests a valid ga4 action


def test_card_param_requirement_platforms_are_all_valid():
    # Drift guard: a requirement keyed on a platform name that a card can never
    # carry (e.g. "meta" vs "meta_ads") is dead validation. Every requirement
    # platform must be a real VALID_PLATFORMS value. (FINDINGS S1 #7)
    bad = sorted({p for (p, _a) in _CARD_PARAM_REQUIREMENTS if p not in VALID_PLATFORMS})
    assert not bad, f"requirement keys reference unknown platforms: {bad}"


def test_meta_ads_card_required_params_are_enforced():
    # Regression: meta_ads cards used to skip param validation because the
    # requirement key was ("meta", ...). A meta_ads campaign-performance card
    # missing ad_account_id must now be rejected.
    card = {
        "key": "meta_perf",
        "title": "Meta Campaign Performance",
        "chart_type": "table",
        "platform": "meta_ads",
        "tool": "marketing_read",
        "action": "get_campaign_performance",
        "params": {"start_date": "2024-01-01", "end_date": "2024-12-31"},  # no ad_account_id
    }
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([card])
    assert "ad_account_id" in str(exc.value)


def test_missing_action_key_is_rejected():
    # The exact shape an agent produced: tool + params but no "action" key at all.
    card = {
        "key": "total_sessions",
        "title": "Total Sessions (2024)",
        "chart_type": "scorecard",
        "platform": "ga4",
        "tool": "analytics_read",
        "params": {
            "platform": "ga4",
            "property_id": "279951751",
            "metrics": ["sessions"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    }
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([card])
    assert "action" in str(exc.value) and "required" in str(exc.value)


def test_search_console_requires_date_range():
    card = {
        "key": "gsc",
        "title": "Search Analytics",
        "chart_type": "table",
        "platform": "search_console",
        "tool": "seo_read",
        "action": "get_search_analytics",
        "params": {"site_url": "https://example.com"},
    }
    with pytest.raises(ValueError) as exc:
        _validate_card_specs([card])
    msg = str(exc.value)
    assert "params.start_date is required" in msg
    assert "params.end_date is required" in msg


def test_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="duplicates an earlier card"):
        _validate_card_specs([_ga4_card(key="same"), _ga4_card(key="same")])


def test_structural_validation_precedes_param_check():
    # Structural errors still raise immediately (not aggregated)
    with pytest.raises(ValueError, match="platform must be a non-empty string"):
        _validate_card_specs(
            [{"key": "x", "title": "T", "chart_type": "table", "platform": "", "tool": "t", "params": {}}]
        )


def test_empty_cards_list_returns_empty():
    assert _validate_card_specs([]) == []
    assert _validate_card_specs(None) == []
