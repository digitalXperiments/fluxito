"""Unit tests for dashboard live-query helpers: scope authorization,
filter-hook override application, and GA4 filter builder."""

import pytest
from google.analytics.data_v1beta.types import Filter, FilterExpression

from app.connectors.ga4 import _build_filter_expression
from app.dashboards.filter_hooks import apply_overrides
from app.dashboards.scope import fingerprint, is_authorized

# ---------------------------------------------------------------------------
# is_authorized — scope matching across platforms
# ---------------------------------------------------------------------------


def test_scope_exact_ga4_property_match():
    scopes = [{"platform": "ga4", "property_id": "279951751"}]
    assert is_authorized(scopes, "ga4", {"property_id": "279951751"}) is True


def test_scope_normalizes_properties_prefix():
    scopes = [{"platform": "ga4", "property_id": "279951751"}]
    # "properties/123" should normalize to "123" in the fingerprint
    assert is_authorized(scopes, "ga4", {"property_id": "properties/279951751"}) is True


def test_scope_wrong_ga4_property():
    scopes = [{"platform": "ga4", "property_id": "111"}]
    assert is_authorized(scopes, "ga4", {"property_id": "222"}) is False


def test_scope_platform_wildcard_allows_any_property():
    scopes = [{"platform": "ga4"}]
    assert is_authorized(scopes, "ga4", {"property_id": "anything"}) is True


def test_scope_wrong_platform():
    scopes = [{"platform": "meta"}]
    assert is_authorized(scopes, "ga4", {"property_id": "279951751"}) is False


def test_scope_empty_list_denies():
    assert is_authorized([], "ga4", {"property_id": "anything"}) is False


def test_scope_bigquery_connection_match():
    scopes = [{"platform": "bigquery", "connection_id": "conn-abc"}]
    assert is_authorized(scopes, "bigquery", {"connection_id": "conn-abc"}) is True
    assert is_authorized(scopes, "bigquery", {"connection_id": "conn-xyz"}) is False


def test_scope_bigquery_dataset_restriction():
    scopes = [{"platform": "bigquery", "connection_id": "c", "dataset_id": "analytics"}]
    ok = {"connection_id": "c", "dataset_id": "analytics"}
    wrong_dataset = {"connection_id": "c", "dataset_id": "marketing"}
    assert is_authorized(scopes, "bigquery", ok) is True
    assert is_authorized(scopes, "bigquery", wrong_dataset) is False


def test_scope_meta_ad_account_match():
    scopes = [{"platform": "meta", "ad_account_id": "act_123"}]
    assert is_authorized(scopes, "meta", {"ad_account_id": "act_123"}) is True
    assert is_authorized(scopes, "meta", {"ad_account_id": "act_999"}) is False


def test_scope_multiple_entries_second_matches():
    scopes = [
        {"platform": "meta", "ad_account_id": "act_999"},
        {"platform": "ga4", "property_id": "279951751"},
    ]
    assert is_authorized(scopes, "ga4", {"property_id": "279951751"}) is True


def test_scope_unknown_platform_falls_back_to_platform_only():
    # Unknown platform → fingerprint is just {"platform": X}, so only platform match is checked.
    scopes = [{"platform": "brand_new"}]
    assert is_authorized(scopes, "brand_new", {"some_field": "anything"}) is True


def test_fingerprint_ga4_normalizes_properties():
    fp = fingerprint("ga4", {"property_id": "properties/123"})
    assert fp == {"platform": "ga4", "property_id": "123"}


def test_fingerprint_unknown_platform():
    fp = fingerprint("some_future_platform", {"foo": "bar"})
    assert fp == {"platform": "some_future_platform"}


# ---------------------------------------------------------------------------
# apply_overrides — filter hook application
# ---------------------------------------------------------------------------


def _base_spec() -> dict:
    return {
        "platform": "ga4",
        "tool": "analytics_read",
        "action": "run_report",
        "params": {
            "property_id": "123",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "filters": {"country": "US"},
        },
        "filter_hooks": {
            "date_range.start": "params.start_date",
            "date_range.end": "params.end_date",
            "country": "params.filters.country",
        },
    }


def test_apply_overrides_nested_date_range():
    spec = _base_spec()
    merged = apply_overrides(
        spec,
        {"date_range": {"start": "2025-06-01", "end": "2025-06-30"}},
    )
    assert merged["params"]["start_date"] == "2025-06-01"
    assert merged["params"]["end_date"] == "2025-06-30"
    # Original untouched
    assert spec["params"]["start_date"] == "2025-01-01"


def test_apply_overrides_flat_form():
    merged = apply_overrides(_base_spec(), {"date_range.start": "2025-12-01"})
    assert merged["params"]["start_date"] == "2025-12-01"
    # End not overridden
    assert merged["params"]["end_date"] == "2025-01-31"


def test_apply_overrides_deep_path():
    merged = apply_overrides(_base_spec(), {"country": "GB"})
    assert merged["params"]["filters"]["country"] == "GB"


def test_apply_overrides_ignores_unknown_keys():
    merged = apply_overrides(_base_spec(), {"mystery_filter": "nope"})
    # Spec unchanged
    assert merged["params"]["start_date"] == "2025-01-01"


def test_apply_overrides_list_index_path():
    spec = {
        "platform": "bigquery",
        "params": {
            "query_params": [
                {"name": "s", "value": "2025-01-01"},
                {"name": "e", "value": "2025-01-31"},
            ]
        },
        "filter_hooks": {
            "date_range.start": "params.query_params[0].value",
            "date_range.end": "params.query_params[1].value",
        },
    }
    merged = apply_overrides(spec, {"date_range": {"start": "2025-06-01", "end": "2025-06-30"}})
    assert merged["params"]["query_params"][0]["value"] == "2025-06-01"
    assert merged["params"]["query_params"][1]["value"] == "2025-06-30"


def test_apply_overrides_no_hooks_returns_copy():
    spec = {"params": {"a": 1}}
    merged = apply_overrides(spec, {"anything": "x"})
    assert merged == spec
    assert merged is not spec  # deep-copied


def test_apply_overrides_no_overrides_returns_copy():
    spec = _base_spec()
    merged = apply_overrides(spec, None)
    assert merged == spec
    assert merged is not spec


# ---------------------------------------------------------------------------
# _build_filter_expression — GA4 connector (unchanged)
# ---------------------------------------------------------------------------


def test_string_filter_exact():
    expr = _build_filter_expression(
        {"filter": {"fieldName": "country", "stringFilter": {"matchType": "EXACT", "value": "US"}}}
    )
    assert isinstance(expr, FilterExpression)
    assert expr.filter.field_name == "country"
    assert expr.filter.string_filter.value == "US"
    assert expr.filter.string_filter.match_type == Filter.StringFilter.MatchType.EXACT


def test_string_filter_contains():
    expr = _build_filter_expression(
        {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/blog"}}}
    )
    assert expr.filter.string_filter.match_type == Filter.StringFilter.MatchType.CONTAINS


def test_in_list_filter():
    expr = _build_filter_expression(
        {
            "filter": {
                "fieldName": "sessionDefaultChannelGroup",
                "inListFilter": {"values": ["Organic Search", "Direct"]},
            }
        }
    )
    assert isinstance(expr, FilterExpression)
    assert list(expr.filter.in_list_filter.values) == ["Organic Search", "Direct"]


def test_numeric_filter_equal():
    expr = _build_filter_expression(
        {
            "filter": {
                "fieldName": "sessions",
                "numericFilter": {"operation": "GREATER_THAN", "value": {"intValue": 100}},
            }
        }
    )
    assert isinstance(expr, FilterExpression)
    assert expr.filter.numeric_filter.operation == Filter.NumericFilter.Operation.GREATER_THAN
    assert expr.filter.numeric_filter.value.int64_value == 100


def test_and_group():
    expr = _build_filter_expression(
        {
            "andGroup": {
                "expressions": [
                    {
                        "filter": {
                            "fieldName": "country",
                            "stringFilter": {"matchType": "EXACT", "value": "US"},
                        }
                    },
                    {
                        "filter": {
                            "fieldName": "deviceCategory",
                            "stringFilter": {"matchType": "EXACT", "value": "mobile"},
                        }
                    },
                ]
            }
        }
    )
    assert isinstance(expr, FilterExpression)
    assert len(expr.and_group.expressions) == 2


def test_or_group():
    expr = _build_filter_expression(
        {
            "orGroup": {
                "expressions": [
                    {
                        "filter": {
                            "fieldName": "country",
                            "stringFilter": {"matchType": "EXACT", "value": "US"},
                        }
                    },
                    {
                        "filter": {
                            "fieldName": "country",
                            "stringFilter": {"matchType": "EXACT", "value": "GB"},
                        }
                    },
                ]
            }
        }
    )
    assert isinstance(expr, FilterExpression)
    assert len(expr.or_group.expressions) == 2


def test_not_expression():
    expr = _build_filter_expression(
        {
            "notExpression": {
                "filter": {
                    "fieldName": "country",
                    "stringFilter": {"matchType": "EXACT", "value": "US"},
                }
            }
        }
    )
    assert isinstance(expr, FilterExpression)
    assert expr.not_expression.filter.field_name == "country"


def test_unsupported_filter_raises():
    with pytest.raises(ValueError, match="Unsupported filter structure"):
        _build_filter_expression({"unknownKey": {}})
