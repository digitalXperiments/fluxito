"""SDR evaluation harness — deterministic checks across value-exchange models.

For each fixture business (a different vertical), this exercises the full server
pipeline end-to-end and asserts:
  1. the diagnostic engine emits the expected findings + readiness, and
  2. the generated skeleton is parse-valid and passes the save validation gate.

This is the "any business works, first pass" guarantee, run in CI. It does not
invoke a model (that is a separate, non-deterministic harness); it pins the
deterministic server behavior the skill depends on.
"""

import pytest

from app.tools.sdr_bootstrap.diagnostics import diagnose
from app.tools.sdr_parser import ParsedDestination, ParsedEvent, parse_sdr_markdown
from app.tools.sdr_templates import get_industry_template
from app.tools.sdr_tools import (
    _build_markdown_skeleton,
    _merge_scan_with_template,
    _validate_sdr_for_save,
)


def _scan(source, roles, events=None, volumes=None, recent=None):
    meta = {}
    if volumes is not None:
        meta["event_volumes"] = volumes
    if recent is not None:
        meta["event_volumes_recent"] = recent
    return {
        "source": source,
        "status": "success",
        "roles": list(roles),
        "events": [{"name": n} for n in (events or [])],
        "raw_metadata": meta,
    }


def _tag(events):
    return _scan("tags", ["tag_inventory"], events=events)


def _vol(volumes, recent=None, conversions=None):
    s = _scan("analytics", ["event_volume", "conversion_config"], events=conversions, volumes=volumes, recent=recent)
    return s


# name → scenario. Each covers a distinct value-exchange model.
SCENARIOS = {
    "ecommerce_dead_purchase": {
        "business_type": "ecommerce",
        "intake": {
            "business_model": "Online store selling physical goods",
            "conversion_definition": "A completed purchase on the order confirmation page",
        },
        "scans": {
            "tags": _tag(["purchase", "add_to_cart", "view_item"]),
            "analytics": _vol(
                {"purchase": 0, "add_to_cart": 1200, "view_item": 9000},
                recent={"purchase": 0, "add_to_cart": 300, "view_item": 2500},
                conversions=["purchase"],
            ),
        },
        "must_find": {"tag_configured_but_no_data", "primary_conversion_unproven"},
        "primary_proven": False,
    },
    "saas_healthy": {
        "business_type": "saas",
        "intake": {
            "business_model": "B2B SaaS on a paid subscription",
            "conversion_definition": "Started a paid subscription",
        },
        "scans": {
            "tags": _tag(["sign_up", "trial_start", "subscribe"]),
            "analytics": _vol(
                {"sign_up": 800, "trial_start": 200, "subscribe": 60},
                recent={"sign_up": 180, "trial_start": 50, "subscribe": 15},
                conversions=["subscribe"],
            ),
        },
        "must_find": set(),  # a clean bill of health is possible
        "primary_proven": True,
    },
    "lead_gen_not_configured": {
        "business_type": "lead_gen",
        "intake": {
            "business_model": "Agency generating leads for sales",
            "conversion_definition": "A qualified lead form submission",
        },
        "scans": {
            "tags": _tag(["form_view", "form_submit"]),
            "analytics": _vol(
                {"form_view": 1500, "form_submit": 300},
                recent={"form_view": 400, "form_submit": 80},
                conversions=[],  # form_submit not set up as a conversion → no ROAS
            ),
        },
        "must_find": {"conversion_not_configured"},
        "primary_proven": True,
    },
    "nonprofit_donation_dead": {
        "business_type": "nonprofit",  # unknown to templates → derive from intake
        "intake": {
            "business_model": "A nonprofit accepting online donations",
            "conversion_definition": "A completed donation",
        },
        "scans": {
            "tags": _tag(["donate", "page_view"]),
            "analytics": _vol(
                {"donate": 0, "page_view": 20000},
                recent={"donate": 0, "page_view": 5000},
                conversions=["donate"],
            ),
        },
        "must_find": {"tag_configured_but_no_data", "primary_conversion_unproven"},
        "primary_proven": False,
    },
    "b2b_booking_recently_stopped": {
        "business_type": "b2b_services",  # unknown to templates → derive from intake
        "intake": {
            "business_model": "B2B consultancy; prospects book a meeting",
            "conversion_definition": "Book a meeting with sales",
        },
        "scans": {
            "tags": _tag(["book_meeting", "page_view"]),
            "analytics": _vol(
                {"book_meeting": 40, "page_view": 6000},
                recent={"book_meeting": 0, "page_view": 1500},
                conversions=["book_meeting"],
            ),
        },
        "must_find": {"event_recently_stopped"},
        "primary_proven": True,  # it did fire over 30d; recency is the flag
    },
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_eval_diagnosis_matches_expectations(name):
    sc = SCENARIOS[name]
    out = diagnose(sc["scans"], sc["intake"])
    found = {f["type"] for f in out["findings"]}
    missing = sc["must_find"] - found
    assert not missing, f"{name}: expected findings missing: {missing} (got {found})"
    assert out["readiness"]["primary_conversion_proven"] is sc["primary_proven"], (
        f"{name}: primary_conversion_proven mismatch"
    )


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_eval_skeleton_is_parse_valid_for_any_vertical(name):
    sc = SCENARIOS[name]
    configured = [e["name"] for e in sc["scans"]["tags"]["events"]]
    scan_events = [
        ParsedEvent(name=n, status="implemented", purpose=f"{n} event", destinations=[ParsedDestination(platform="ga4")])
        for n in configured
    ]
    merged = _merge_scan_with_template(scan_events, get_industry_template(sc["business_type"]))
    skeleton = _build_markdown_skeleton(
        project_name=f"Eval {name}",
        project_id="eval",
        business_type=sc["business_type"],
        events=merged,
        intake_answers=sc["intake"],
    )
    parsed = parse_sdr_markdown(skeleton)
    assert parsed.events, f"{name}: skeleton produced no parseable events"
    # The skeleton must never fail the save validation gate's hard errors.
    assert _validate_sdr_for_save(skeleton, parsed)["errors"] == [], f"{name}: skeleton fails validation"
