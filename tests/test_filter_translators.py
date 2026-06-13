"""Per-platform filter translation — one assertion per cell of the spec table.

GA4 shapes match app/connectors/ga4.py:_build_filter_expression exactly.
"""

import pytest

from app.dashboards.filter_translators import (
    UnsupportedFilterError,
    apply_card_filters,
    resolve_active_value,
    translate,
)

# ----------------------------------------------------------------------- GA4


def test_ga4_single_select_exact():
    args: dict = {}
    translate("ga4", "single_select", "dimension_filter.country", "US", args)
    assert args["dimension_filter"] == {
        "filter": {"fieldName": "country", "stringFilter": {"matchType": "EXACT", "value": "US"}}
    }


def test_ga4_search_contains():
    args: dict = {}
    translate("ga4", "search", "dimension_filter.pagePath", "checkout", args)
    assert args["dimension_filter"]["filter"]["stringFilter"]["matchType"] == "CONTAINS"
    assert args["dimension_filter"]["filter"]["stringFilter"]["value"] == "checkout"


def test_ga4_multi_select_inlist():
    args: dict = {}
    translate("ga4", "multi_select", "dimension_filter.channel", ["Organic", "Paid"], args)
    assert args["dimension_filter"]["filter"]["inListFilter"]["values"] == ["Organic", "Paid"]


def test_ga4_two_filters_merge_into_andgroup():
    args: dict = {}
    translate("ga4", "single_select", "dimension_filter.country", "US", args)
    translate("ga4", "search", "dimension_filter.pagePath", "checkout", args)
    grp = args["dimension_filter"]["andGroup"]["expressions"]
    assert len(grp) == 2
    assert grp[0]["filter"]["fieldName"] == "country"
    assert grp[1]["filter"]["fieldName"] == "pagePath"


def test_ga4_toggle_applies_exact_filters():
    args: dict = {}
    translate("ga4", "toggle", "ignored", {"newVsReturning": "new"}, args)
    assert args["dimension_filter"]["filter"]["fieldName"] == "newVsReturning"
    assert args["dimension_filter"]["filter"]["stringFilter"]["value"] == "new"


def test_ga4_toggle_off_is_noop():
    args: dict = {}
    translate("ga4", "toggle", "ignored", None, args)
    assert args == {}


def test_ga4_number_range_metric_filter():
    args: dict = {}
    translate("ga4", "number_range", "metric_filter.sessions", {"min": 100, "max": 5000}, args)
    exprs = args["metric_filter"]["andGroup"]["expressions"]
    assert exprs[0]["filter"]["numericFilter"]["operation"] == "GREATER_THAN_OR_EQUAL"
    assert exprs[0]["filter"]["numericFilter"]["value"] == {"intValue": 100}
    assert exprs[1]["filter"]["numericFilter"]["operation"] == "LESS_THAN_OR_EQUAL"


def test_ga4_empty_value_noop():
    args: dict = {}
    translate("ga4", "single_select", "dimension_filter.country", "", args)
    translate("ga4", "multi_select", "dimension_filter.channel", [], args)
    translate("ga4", "number_range", "metric_filter.sessions", {"min": None, "max": None}, args)
    assert args == {}


# ----------------------------------------------------------------- warehouse


def test_warehouse_single_select_quoted():
    args = {"query": "SELECT * FROM t WHERE country = {country}"}
    translate("warehouse", "single_select", "country", "US", args)
    assert args["query"].endswith("WHERE country = 'US'")


def test_warehouse_multi_select_in_clause():
    args = {"query": "SELECT * FROM t WHERE channel IN ({channel})"}
    translate("warehouse", "multi_select", "channel", ["a", "b"], args)
    assert "IN ('a', 'b')" in args["query"]


def test_warehouse_search_ilike():
    args = {"query": "SELECT * FROM t WHERE page ILIKE {q}"}
    translate("warehouse", "search", "q", "checkout", args)
    assert "ILIKE '%checkout%'" in args["query"]


def test_warehouse_number_range_min_max_tokens():
    args = {"query": "... WHERE sessions BETWEEN {s_min} AND {s_max}"}
    translate("warehouse", "number_range", "s", {"min": 100, "max": 5000}, args)
    assert "BETWEEN 100 AND 5000" in args["query"]


def test_warehouse_escapes_single_quotes():
    args = {"query": "WHERE name = {name}"}
    translate("warehouse", "single_select", "name", "O'Brien", args)
    assert "'O''Brien'" in args["query"]


def test_warehouse_number_range_rejects_injection():
    args = {"query": "WHERE x BETWEEN {x_min} AND {x_max}"}
    with pytest.raises(ValueError):
        translate("warehouse", "number_range", "x", {"min": "1); DROP TABLE t;--", "max": 9}, args)


# ----------------------------------------------------------------- marketing


def test_marketing_single_select_sets_param():
    args: dict = {}
    translate("meta", "single_select", "filters.campaign", "Summer", args)
    assert args["filters"]["campaign"] == "Summer"


def test_marketing_search_unsupported():
    with pytest.raises(UnsupportedFilterError):
        translate("meta", "search", "x", "abc", {})


def test_marketing_number_range_unsupported():
    with pytest.raises(UnsupportedFilterError):
        translate("tiktok", "number_range", "x", {"min": 1, "max": 2}, {})


# -------------------------------------------------------- orchestration


def test_resolve_multi_select_splits_csv():
    spec = {"type": "multi_select"}
    assert resolve_active_value("multi_select", "ch", spec, {"ch": "Organic,Paid"}) == [
        "Organic",
        "Paid",
    ]


def test_resolve_toggle_on_returns_applies():
    spec = {"type": "toggle", "toggle": {"applies": {"newVsReturning": "new"}}}
    assert resolve_active_value("toggle", "nu", spec, {"nu": "1"}) == {"newVsReturning": "new"}
    assert resolve_active_value("toggle", "nu", spec, {"nu": "0"}) is None


def test_resolve_number_range_reads_min_max():
    assert resolve_active_value("number_range", "s", {}, {"s_min": "100", "s_max": "5000"}) == {
        "min": "100",
        "max": "5000",
    }


def test_apply_card_filters_ga4_single_select():
    hooks = {"country": "dimension_filter.country", "date_range.start": "start_date"}
    specs = {"country": {"key": "country", "type": "single_select"}}
    args: dict = {}
    handled = apply_card_filters(hooks, specs, {"country": "US"}, "ga4", args)
    assert handled == {"country"}
    assert args["dimension_filter"]["filter"]["fieldName"] == "country"


def test_apply_card_filters_skips_legacy_and_date_keys():
    hooks = {"date_range.start": "start_date", "legacy_dim": "filters.x"}
    specs = {"country": {"key": "country", "type": "single_select"}}
    args: dict = {}
    handled = apply_card_filters(hooks, specs, {"legacy_dim": "v"}, "ga4", args)
    assert handled == set()  # legacy key not in specs; date key skipped
    assert args == {}


def test_apply_card_filters_unsupported_combo_skipped_not_raised():
    hooks = {"q": "q"}
    specs = {"q": {"key": "q", "type": "search"}}
    args: dict = {}
    # marketing platform can't express search -> skipped, no raise
    handled = apply_card_filters(hooks, specs, {"q": "abc"}, "meta", args)
    assert handled == set()
