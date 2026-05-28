"""Unit tests for the platform-agnostic SDR diagnostic engine and capability roles."""

import asyncio
from types import SimpleNamespace


def test_sources_declare_capability_roles():
    from app.tools.sdr_bootstrap.registry import (
        DATA_SOURCE_REGISTRY,
        ROLE_CONVERSION_CONFIG,
        ROLE_EVENT_VOLUME,
        ROLE_TAG_INVENTORY,
    )

    ga4 = DATA_SOURCE_REGISTRY["ga4"]
    gtm = DATA_SOURCE_REGISTRY["gtm"]
    ads = DATA_SOURCE_REGISTRY["google_ads"]
    assert ROLE_EVENT_VOLUME in ga4.provides and ROLE_CONVERSION_CONFIG in ga4.provides
    assert gtm.provides == frozenset({ROLE_TAG_INVENTORY})
    assert ads.provides == frozenset({ROLE_CONVERSION_CONFIG})


def test_scan_dict_includes_roles():
    from app.tools.sdr_bootstrap.registry import ROLE_EVENT_VOLUME, SDRSourceScan

    scan = SDRSourceScan(source="ga4", status="success", roles=frozenset({ROLE_EVENT_VOLUME}))
    assert scan.to_dict()["roles"] == [ROLE_EVENT_VOLUME]


def test_ga4_scan_captures_event_volumes(monkeypatch):
    import app.app_state as state
    from app.tools.sdr_bootstrap.registry import GA4DataSource

    class FakeGA4:
        async def get_conversion_events(self, conn_id, prop_id):
            return {"conversion_events": [{"event_name": "purchase"}]}

        async def list_custom_dimensions(self, conn_id, prop_id):
            return {"custom_dimensions": []}

        async def list_events(self, conn_id, prop_id, start, end):
            return {
                "events": [
                    {"event_name": "purchase", "event_count": 0},
                    {"event_name": "view_item", "event_count": 4200},
                ]
            }

    monkeypatch.setattr(state, "ga4_connector", FakeGA4(), raising=False)
    ctx = SimpleNamespace(
        has_ga4=True,
        ga4_properties=[{"property_id": "123"}],
        connections=[SimpleNamespace(id="c1", provider="google")],
    )
    scan = asyncio.run(GA4DataSource().scan(ctx))
    assert scan.raw_metadata["event_volumes"] == {"purchase": 0, "view_item": 4200}


# ── diagnostic engine ───────────────────────────────────────────────────────


def _scan(source, roles, events=None, volumes=None, status="success", errors=None):
    return {
        "source": source,
        "status": status,
        "roles": list(roles),
        "events": [{"name": n} for n in (events or [])],
        "raw_metadata": ({"event_volumes": volumes} if volumes is not None else {}),
        "errors": errors or [],
    }


def test_diagnose_flags_tag_configured_but_no_data_gtm_ga4():
    from app.tools.sdr_bootstrap.diagnostics import diagnose

    scans = {
        "gtm": _scan("gtm", ["tag_inventory"], events=["purchase", "view_item"]),
        "ga4": _scan(
            "ga4",
            ["event_volume", "conversion_config"],
            events=["purchase"],
            volumes={"purchase": 0, "view_item": 4200},
        ),
    }
    out = diagnose(scans, {"conversion_definition": "completed purchase"})
    types = {f["type"] for f in out["findings"]}
    assert "tag_configured_but_no_data" in types
    pf = next(f for f in out["findings"] if f["type"] == "tag_configured_but_no_data")
    assert "purchase" in pf["affected_events"]
    assert pf["severity"] == "critical"
    assert out["readiness"]["primary_conversion_proven"] is False


def test_diagnose_is_platform_agnostic_warehouse_role():
    from app.tools.sdr_bootstrap.diagnostics import diagnose

    scans = {
        "server_tags": _scan("server_tags", ["tag_inventory"], events=["lead_qualified"]),
        "warehouse": _scan("warehouse", ["event_volume"], volumes={"lead_qualified": 0}),
    }
    out = diagnose(scans, {"conversion_definition": "a qualified lead"})
    assert any(f["type"] == "tag_configured_but_no_data" for f in out["findings"])


def test_diagnose_reports_connector_errors_and_unfilled_roles():
    from app.tools.sdr_bootstrap.diagnostics import diagnose

    scans = {
        "google_ads": _scan("google_ads", ["conversion_config"], status="failed", errors=["token expired"])
    }
    out = diagnose(scans, {})
    assert any(f["type"] == "connector_error" for f in out["findings"])
    assert "tag_inventory" in out["readiness"]["unfilled_roles"]
    assert "event_volume" in out["readiness"]["unfilled_roles"]


def test_diagnose_degrades_without_volume_provider():
    from app.tools.sdr_bootstrap.diagnostics import diagnose

    scans = {"gtm": _scan("gtm", ["tag_inventory"], events=["purchase"])}
    out = diagnose(scans, {"conversion_definition": "purchase"})
    assert not any(f["type"] == "tag_configured_but_no_data" for f in out["findings"])
    assert out["readiness"]["events_volume_proven"] is None
