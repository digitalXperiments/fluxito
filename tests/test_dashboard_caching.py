"""Live-data cache key: busts on card / filter / compare changes; stable otherwise."""

from types import SimpleNamespace

from app.api.dashboard_routes import _cards_signature, _dashdata_cache_key


def _card(qp, refreshed="t0"):
    return SimpleNamespace(query_params=qp, chart_type="line", chart_config={}, refreshed_at=refreshed)


def _key(cards_sig="sig", filters=None, platforms=None):
    return _dashdata_cache_key("slug", False, 2, "v1", filters or {}, platforms or set(), cards_sig)


def test_cards_signature_changes_on_card_edit():
    a = [_card({"metrics": ["sessions"]})]
    b = [_card({"metrics": ["users"]})]
    assert _cards_signature(a) != _cards_signature(b)


def test_cards_signature_changes_on_refresh():
    a = [_card({"x": 1}, refreshed="t0")]
    b = [_card({"x": 1}, refreshed="t1")]
    assert _cards_signature(a) != _cards_signature(b)


def test_cards_signature_stable():
    a = [_card({"x": 1})]
    b = [_card({"x": 1})]
    assert _cards_signature(a) == _cards_signature(b)


def test_key_busts_on_card_change():
    assert _key(cards_sig="s1") != _key(cards_sig="s2")


def test_key_busts_on_filter_change():
    assert _key(filters={"country": "US"}) != _key(filters={"country": "AE"})


def test_key_busts_on_compare_toggle():
    # compare params live in filter_overrides, so they change the key
    assert _key(filters={}) != _key(filters={"compare": "previous_period"})


def test_key_stable_for_same_inputs():
    assert _key(filters={"country": "US"}) == _key(filters={"country": "US"})
