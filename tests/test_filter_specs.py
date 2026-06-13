import pytest

from app.dashboards.filter_specs import (
    FilterSpecError,
    synthesize_filters,
    validate_filters,
)


def test_empty_returns_empty():
    assert validate_filters(None) == []
    assert validate_filters([]) == []


def test_valid_multi_select_default_normalized():
    out = validate_filters(
        [
            {
                "key": "ch",
                "label": "Channel",
                "type": "multi_select",
                "options": {"source": "static", "values": ["Organic", "Paid"]},
            }
        ]
    )
    assert out[0]["default"] == []  # multi_select default normalized to []
    assert out[0]["options"]["values"] == ["Organic", "Paid"]


def test_single_select_label_falls_back_to_key():
    out = validate_filters(
        [{"key": "country", "type": "single_select", "options": {"source": "static", "values": [""]}}]
    )
    assert out[0]["label"] == "country"
    assert out[0]["default"] == ""


def test_rejects_unknown_type():
    with pytest.raises(FilterSpecError):
        validate_filters([{"key": "x", "label": "X", "type": "slider"}])


def test_rejects_missing_key():
    with pytest.raises(FilterSpecError):
        validate_filters([{"label": "X", "type": "search"}])


def test_rejects_duplicate_keys():
    f = {
        "key": "c",
        "label": "C",
        "type": "single_select",
        "options": {"source": "static", "values": ["a"]},
    }
    with pytest.raises(FilterSpecError):
        validate_filters([f, dict(f)])


def test_toggle_requires_applies():
    with pytest.raises(FilterSpecError):
        validate_filters([{"key": "new", "label": "New", "type": "toggle"}])


def test_toggle_valid():
    out = validate_filters(
        [
            {
                "key": "new",
                "label": "New users",
                "type": "toggle",
                "toggle": {"applies": {"new_vs_returning": "new"}},
            }
        ]
    )
    assert out[0]["toggle"]["applies"] == {"new_vs_returning": "new"}
    assert out[0]["default"] is False


def test_number_range_default_shape():
    out = validate_filters([{"key": "s", "label": "Sessions", "type": "number_range"}])
    assert out[0]["default"] == {"min": None, "max": None}


def test_warehouse_options_need_card_and_column():
    with pytest.raises(FilterSpecError):
        validate_filters([{"key": "c", "type": "single_select", "options": {"source": "warehouse"}}])


def test_select_static_needs_values_list():
    with pytest.raises(FilterSpecError):
        validate_filters([{"key": "c", "type": "single_select", "options": {"source": "static"}}])


def test_search_passes_through():
    out = validate_filters([{"key": "page", "label": "Page", "type": "search"}])
    assert out[0]["type"] == "search"
    assert out[0]["default"] == ""


def test_too_many_filters_rejected():
    many = [{"key": f"k{i}", "type": "search", "label": str(i)} for i in range(21)]
    with pytest.raises(FilterSpecError):
        validate_filters(many)


# ----------------------------------------------------- synthesize_filters (legacy)


def test_synthesize_from_legacy_hooks_and_options():
    cards = [
        {
            "query_params": {
                "filter_hooks": {"date_range.start": "start_date", "country": "filters.country"},
                "filter_options": {"country": ["", "US", "AE"]},
            }
        },
        {  # second card adds another country value + a new dim
            "query_params": {
                "filter_hooks": {"country": "filters.country", "device": "filters.device"},
                "filter_options": {"country": ["EG"], "device": ["", "mobile"]},
            }
        },
    ]
    out = synthesize_filters(cards)
    keys = {f["key"] for f in out}
    assert keys == {"country", "device"}  # date key skipped
    country = next(f for f in out if f["key"] == "country")
    assert country["type"] == "single_select"
    assert country["options"]["values"] == ["", "US", "AE", "EG"]  # merged + deduped
    assert country["label"] == "Country"


def test_synthesize_empty_when_no_hooks():
    assert synthesize_filters([{"query_params": {}}]) == []
    assert synthesize_filters([]) == []
