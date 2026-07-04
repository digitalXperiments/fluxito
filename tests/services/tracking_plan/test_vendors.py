# tests/services/tracking_plan/test_vendors.py
"""Tests for the vendor catalog (app/services/tracking_plan/vendors.py)."""

from app.services.tracking_plan.vendors import TP_VENDOR_CATALOG, get_vendor_catalog


def test_get_vendor_catalog_shape():
    """get_vendor_catalog returns a dict with destinations and source_platforms lists."""
    catalog = get_vendor_catalog()
    assert isinstance(catalog, dict)
    assert "destinations" in catalog
    assert "source_platforms" in catalog
    assert isinstance(catalog["destinations"], list)
    assert isinstance(catalog["source_platforms"], list)
    assert len(catalog["destinations"]) > 0
    assert len(catalog["source_platforms"]) > 0


def test_destination_entries_have_required_keys():
    """Every destination entry has slug, display_name, category, and source."""
    catalog = get_vendor_catalog()
    for entry in catalog["destinations"]:
        assert "slug" in entry, f"Missing 'slug' in {entry}"
        assert "display_name" in entry, f"Missing 'display_name' in {entry}"
        assert "category" in entry, f"Missing 'category' in {entry}"
        assert "source" in entry, f"Missing 'source' in {entry}"
        assert entry["source"] in {
            "connector",
            "audit",
            "curated",
        }, f"Unknown source tag {entry['source']!r} in {entry}"


def test_slugs_are_unique():
    """No duplicate slugs in the destination catalog."""
    catalog = get_vendor_catalog()
    slugs = [e["slug"] for e in catalog["destinations"]]
    assert len(slugs) == len(set(slugs)), f"Duplicate slugs found: {sorted(slugs)}"


def test_moengage_present_as_connector():
    """MoEngage is present in the vendor catalog with source='connector'."""
    catalog = get_vendor_catalog()
    slugs_by_name = {e["slug"]: e for e in catalog["destinations"]}
    assert "moengage" in slugs_by_name, "moengage not found in vendor catalog"
    # MoEngage is now a first-class connector registered in rate_limits.CATALOG
    assert slugs_by_name["moengage"]["source"] == "connector"


def test_ga4_present():
    """GA4 is present (contributed by the connector or audit registry)."""
    catalog = get_vendor_catalog()
    slugs = {e["slug"] for e in catalog["destinations"]}
    assert "ga4" in slugs, "ga4 not found in vendor catalog"


def test_meta_slug_normalized():
    """meta_pixel audit slug is normalized to 'meta' — only 'meta' (not 'meta_pixel') is in the catalog."""
    catalog = get_vendor_catalog()
    slugs = {e["slug"] for e in catalog["destinations"]}
    # The audit slug 'meta_pixel' must be absent; only the normalized 'meta' slug survives.
    assert "meta_pixel" not in slugs, "'meta_pixel' should be normalized away; use 'meta'"
    # 'meta' may or may not be present depending on connector catalog, but 'meta_pixel' must not be.


def test_source_platforms_have_required_keys():
    """Every source platform entry has slug and display_name."""
    catalog = get_vendor_catalog()
    for sp in catalog["source_platforms"]:
        assert "slug" in sp, f"Missing 'slug' in {sp}"
        assert "display_name" in sp, f"Missing 'display_name' in {sp}"


def test_expected_source_platform_kinds_present():
    """The standard source kinds (web, ios, android, server) are in source_platforms."""
    catalog = get_vendor_catalog()
    slugs = {sp["slug"] for sp in catalog["source_platforms"]}
    for expected in ("web", "ios", "android", "server"):
        assert expected in slugs, f"Expected source platform '{expected}' not found"


def test_tp_vendor_catalog_module_level_cache():
    """TP_VENDOR_CATALOG module-level list matches get_vendor_catalog()['destinations']."""
    catalog = get_vendor_catalog()
    assert catalog["destinations"] == TP_VENDOR_CATALOG
