"""Tests for the connector rate-limits catalog and its UI wiring.

Pure + file-based (no DB), so these run fast and deterministically. They guard
the catalog's integrity, the connected/available partitioning, and that both
surfaces (Project Settings tab + Home section) stay wired to the catalog.
"""

from pathlib import Path
from types import SimpleNamespace

from app.api.google_oauth_routes import GRANULAR_CONNECTOR_CATALOG
from app.connectors import rate_limits as rl

SETTINGS_TEMPLATE = Path("app/templates/projects/settings.html")
HOME_TEMPLATE = Path("app/templates/dashboard_home.html")


# ── Catalog integrity ────────────────────────────────────────────────────


def test_catalog_keys_are_unique():
    keys = [c.key for c in rl.CATALOG]
    assert len(keys) == len(set(keys))


def test_every_connector_is_well_formed():
    for c in rl.CATALOG:
        assert c.name, c.key
        assert c.headline, c.key
        assert c.category in (
            rl.CAT_ANALYTICS,
            rl.CAT_ADVERTISING,
            rl.CAT_TAGGING,
            rl.CAT_SEARCH,
            rl.CAT_WAREHOUSE,
            rl.CAT_MARKETING,
        ), c.key
        assert c.docs_url.startswith("https://"), c.key
        assert c.confidence in (rl.HIGH, rl.MEDIUM, rl.LOW), c.key
        assert c.consumption_note, c.key
        assert c.error_behavior, c.key
        assert c.reviewed, c.key
        assert c.limits, f"{c.key} has no documented limits"
        for limit in c.limits:
            assert limit.name and limit.value and limit.window and limit.scope, (c.key, limit)


def test_core_google_and_meta_connectors_are_present():
    keys = {c.key for c in rl.CATALOG}
    for expected in ("ga4", "gtm", "bigquery", "google_ads", "search_console", "meta_ads"):
        assert expected in keys


def test_catalog_covers_every_granular_connector():
    """Every connector the connect-counter knows about has a rate-limit entry.

    Adobe is one row in the granular catalog (has_adobe_analytics OR
    has_adobe_launch) but two rate-limit entries; either flag satisfying it is
    enough. Marketo has a rate-limit entry without a granular-catalog row.
    """
    rl_flags = {f for c in rl.CATALOG for f in c.flags}
    for _key, _label, attrs in GRANULAR_CONNECTOR_CATALOG:
        assert any(a in rl_flags for a in attrs), f"no rate-limit entry maps to {attrs}"


def test_no_catalog_flag_is_unknown_to_the_connect_counter():
    """Catalog flags use the same has_* vocabulary as the connect counter.

    has_marketo is the one intentional addition (Marketo isn't in the granular
    counter), so it's allowed through.
    """
    granular_flags = {a for _k, _l, attrs in GRANULAR_CONNECTOR_CATALOG for a in attrs}
    rl_flags = {f for c in rl.CATALOG for f in c.flags}
    assert rl_flags - granular_flags == {"has_marketo"}


# ── Helpers ──────────────────────────────────────────────────────────────


def test_connected_keys_maps_flags_to_connectors():
    flags = SimpleNamespace(has_ga4=True, has_meta=True, has_bq=True)
    assert rl.connected_keys(flags) == {"ga4", "meta_ads", "bigquery"}


def test_connected_keys_tolerates_missing_attributes():
    # An object with none of the has_* attrs resolves to no connectors.
    assert rl.connected_keys(SimpleNamespace()) == set()


def test_adobe_analytics_and_launch_split_into_two_connectors():
    flags = SimpleNamespace(has_adobe_analytics=True, has_adobe_launch=True)
    assert rl.connected_keys(flags) == {"adobe_analytics", "adobe_launch"}


def test_partition_is_exhaustive_and_ordered():
    connected = {"ga4", "meta_ads"}
    conn, avail = rl.partition(connected)
    assert {c.key for c in conn} == connected
    assert len(conn) + len(avail) == len(rl.CATALOG)
    # Catalog order is preserved within each bucket.
    catalog_order = [c.key for c in rl.CATALOG]
    assert [c.key for c in avail] == [k for k in catalog_order if k not in connected]


def test_to_view_returns_json_friendly_dicts():
    view = rl.to_view(list(rl.CATALOG))
    assert len(view) == len(rl.CATALOG)
    first = view[0]
    assert isinstance(first, dict)
    assert isinstance(first["flags"], list)
    assert isinstance(first["limits"], list)
    assert isinstance(first["limits"][0], dict)
    assert {"name", "value", "window", "scope"} <= set(first["limits"][0])


def test_by_key_roundtrips():
    assert rl.by_key("ga4").name == "Google Analytics 4"
    assert rl.by_key("nope") is None


# ── Settings tab wiring ──────────────────────────────────────────────────


def test_settings_has_api_limits_tab_between_connections_and_notifications():
    src = SETTINGS_TEMPLATE.read_text()
    tabs = src[src.index('<div class="ps-tabs"') : src.index("{# ── MEMBERS TAB")]
    assert ">API Limits</button>" in tabs
    assert tabs.index(">Connections</button>") < tabs.index(">API Limits</button>")
    assert tabs.index(">API Limits</button>") < tabs.index(">Notifications</button>")


def test_settings_limits_panel_renders_catalog():
    src = SETTINGS_TEMPLATE.read_text()
    assert 'data-panel="limits"' in src
    assert "macro rl_card" in src
    assert "rate_limits_connected" in src
    assert "rate_limits_available" in src
    assert "rate_limits_reviewed" in src
    # The card surfaces the consumption estimate, limits table and docs link.
    assert "consumption_note" in src
    assert "c.limits" in src
    assert "c.docs_url" in src


# ── Home section wiring ──────────────────────────────────────────────────


def test_home_renders_connected_limits_section_with_deep_link():
    src = HOME_TEMPLATE.read_text()
    assert "rate_limits_connected" in src
    assert "rl-home-grid" in src
    assert "/settings#limits" in src  # deep-links into the Settings tab
    assert "consumption_note" in src


# ── Drift checker (app/connectors/rate_limits_drift.py) ───────────────────


def test_drift_audit_covers_every_connector():
    from app.connectors.rate_limits_drift import audit

    rows = audit(max_age_days=180)
    assert {r["key"] for r in rows} == {c.key for c in rl.CATALOG}
    for r in rows:
        assert {"key", "name", "docs_url", "reviewed", "age_days", "confidence", "stale"} <= set(r)


def test_drift_audit_staleness_is_threshold_and_date_driven():
    import datetime as dt

    from app.connectors.rate_limits_drift import audit

    # Threshold extremes: -1 day flags everything, a huge window flags nothing.
    assert all(r["stale"] for r in audit(max_age_days=-1))
    assert not any(r["stale"] for r in audit(max_age_days=10**6))
    # Date-driven: years after every reviewed date, all entries are overdue.
    assert all(r["stale"] for r in audit(max_age_days=180, today=dt.date(2035, 1, 1)))


def test_drift_prompt_is_self_contained():
    from app.connectors.rate_limits_drift import DRIFT_PROMPT

    assert "rate_limits.py" in DRIFT_PROMPT
    assert "CHANGED" in DRIFT_PROMPT and "STALE" in DRIFT_PROMPT and "UNVERIFIED" in DRIFT_PROMPT
