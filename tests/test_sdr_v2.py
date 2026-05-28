from types import SimpleNamespace


def test_intake_questions_are_stable_and_versioned():
    from app.tools.sdr_bootstrap.intake import INTAKE_VERSION, get_intake_questions

    questions = get_intake_questions()

    assert INTAKE_VERSION == "v1"
    assert [q["key"] for q in questions] == [
        "business_model",
        "primary_kpis",
        "conversion_definition",
        "key_journeys",
        "privacy_consent",
        "ownership_complexity",
        "anything_else",
    ]
    assert all(q["question"] for q in questions)
    assert questions[-1]["optional"] is True


def test_source_fingerprint_is_stable_for_resource_order():
    from app.tools.sdr_bootstrap.registry import compute_source_fingerprint

    ctx_a = SimpleNamespace(
        connections=[SimpleNamespace(id="2", provider="google"), SimpleNamespace(id="1", provider="meta")],
        ga4_properties=[{"property_id": "222"}, {"property_id": "111"}],
        gtm_containers=[{"account_id": "a", "container_id": "z"}],
        ads_accounts=[{"customer_id": "999"}],
        search_console_sites=[],
    )
    ctx_b = SimpleNamespace(
        connections=[SimpleNamespace(id="1", provider="meta"), SimpleNamespace(id="2", provider="google")],
        ga4_properties=[{"property_id": "111"}, {"property_id": "222"}],
        gtm_containers=[{"container_id": "z", "account_id": "a"}],
        ads_accounts=[{"customer_id": "999"}],
        search_console_sites=[],
    )

    assert compute_source_fingerprint(ctx_a) == compute_source_fingerprint(ctx_b)


def test_registry_reports_unavailable_sources_without_raising():
    from app.tools.sdr_bootstrap.registry import get_available_sources

    ctx = SimpleNamespace(
        has_ga4=False,
        has_gtm=False,
        has_ads=False,
        connections=[],
        ga4_properties=[],
        gtm_containers=[],
        ads_accounts=[],
        search_console_sites=[],
    )

    assert get_available_sources(ctx) == []


def test_business_type_defaults_to_general_not_ecommerce():
    from app.tools.sdr_tools import _infer_business_type

    # No recognizable signals → must NOT assume ecommerce
    assert _infer_business_type([]) == "general"


def test_generate_response_includes_findings_and_readiness():
    import app.tools.sdr_tools as t
    from app.tools.sdr_bootstrap.registry import (
        ROLE_EVENT_VOLUME,
        ROLE_TAG_INVENTORY,
        SDRSourceScan,
    )
    from app.tools.sdr_parser import ParsedEvent

    scans = {
        "gtm": SDRSourceScan(
            source="gtm", status="success",
            events=[ParsedEvent(name="purchase")], roles=frozenset({ROLE_TAG_INVENTORY}),
        ),
        "ga4": SDRSourceScan(
            source="ga4", status="success",
            raw_metadata={"event_volumes": {"purchase": 0}}, roles=frozenset({ROLE_EVENT_VOLUME}),
        ),
    }
    block = t._diagnostics_block(scans, {"conversion_definition": "purchase"}, ["purchase"])
    assert "findings" in block and "readiness" in block
    assert any(f["type"] == "tag_configured_but_no_data" for f in block["findings"])


def test_synthesis_playbook_renders_findings():
    from app.tools.sdr_tools import _build_synthesis_playbook

    findings = [{"type": "tag_configured_but_no_data", "severity": "critical",
                 "summary": "`purchase` configured but no data", "fix_location": "website"}]
    pb = _build_synthesis_playbook("X", "general", None, findings=findings)
    assert "DIAGNOSTIC FINDINGS" in pb
    assert "purchase" in pb


def test_tracking_plan_exposes_sdr_v2_actions():
    from app.tools.unified import TRACKING_PLAN_ROUTES

    assert TRACKING_PLAN_ROUTES["save"] == ("save_sdr", None)
    assert TRACKING_PLAN_ROUTES["refresh_sources"] == ("refresh_sdr_sources", None)
    assert TRACKING_PLAN_ROUTES["capture_intake"] == ("capture_sdr_intake", None)
    assert TRACKING_PLAN_ROUTES["get_intake"] == ("get_sdr_intake", None)
    assert TRACKING_PLAN_ROUTES["list_sources"] == ("list_sdr_sources", None)
    assert TRACKING_PLAN_ROUTES["diagnose"] == ("diagnose_sdr", None)


# ───────────────────────────────────────────────────────────────────────────
# Ecommerce template completeness
# ───────────────────────────────────────────────────────────────────────────


def test_ecommerce_template_covers_full_ga4_funnel():
    from app.tools.sdr_templates import get_industry_template

    names = [e.name for e in get_industry_template("ecommerce")]
    expected_funnel = [
        "view_item_list",
        "select_item",
        "view_item",
        "add_to_cart",
        "view_cart",
        "begin_checkout",
        "add_shipping_info",
        "add_payment_info",
        "purchase",
        "refund",
    ]
    for name in expected_funnel:
        assert name in names, f"ecommerce template missing {name}"
    purchase = next(e for e in get_industry_template("ecommerce") if e.name == "purchase")
    pnames = {p.name for p in purchase.parameters}
    assert {"transaction_id", "value", "currency", "items"} <= pnames


# ───────────────────────────────────────────────────────────────────────────
# Registry: connected-but-unsupported visibility
# ───────────────────────────────────────────────────────────────────────────


def test_summary_surfaces_connected_but_unsupported():
    from app.tools.sdr_bootstrap.registry import connected_sources_summary

    ctx = SimpleNamespace(
        has_ga4=True,
        has_gtm=False,
        has_ads=False,
        has_meta=True,
        has_bq=True,
        ga4_properties=[{"property_id": "1"}],
        gtm_containers=[],
        ads_accounts=[],
        search_console_sites=[],
    )
    summary = connected_sources_summary(ctx, {})
    assert "meta" in summary["connected_but_unsupported"]
    assert "bigquery" in summary["connected_but_unsupported"]
    assert "ga4" not in summary["connected_but_unsupported"]
    assert set(summary["supported_sources"]) == {"ga4", "gtm", "google_ads"}


# ───────────────────────────────────────────────────────────────────────────
# Merge: scan events win, template fills gaps as planned
# ───────────────────────────────────────────────────────────────────────────


def test_merge_scan_with_template_precedence():
    from app.tools.sdr_parser import ParsedEvent
    from app.tools.sdr_tools import _merge_scan_with_template

    scanned = [ParsedEvent(name="purchase", status="implemented", purpose="live")]
    template = [
        ParsedEvent(name="purchase", status="planned", purpose="tmpl"),
        ParsedEvent(name="view_item", status="planned", purpose="tmpl"),
    ]
    merged = {e.name: e for e in _merge_scan_with_template(scanned, template)}
    assert merged["purchase"].status == "implemented"  # scan wins
    assert merged["purchase"].purpose == "live"
    assert merged["view_item"].status == "planned"  # template fills gap


# ───────────────────────────────────────────────────────────────────────────
# Markdown skeleton is parse-valid and round-trips
# ───────────────────────────────────────────────────────────────────────────


def _skeleton():
    from app.tools.sdr_parser import ParsedDestination, ParsedEvent
    from app.tools.sdr_templates import get_industry_template
    from app.tools.sdr_tools import _build_markdown_skeleton, _merge_scan_with_template

    scanned = [
        ParsedEvent(
            name="purchase",
            purpose="Completed order",
            status="implemented",
            destinations=[ParsedDestination(platform="ga4")],
        )
    ]
    # Mirror _generate_sdr_v2: live scan merged with the industry template.
    events = _merge_scan_with_template(scanned, get_industry_template("ecommerce"))
    return _build_markdown_skeleton(
        project_name="BMK Eco Farms",
        project_id="proj-1",
        business_type="ecommerce",
        events=events,
        intake_answers={
            "business_model": "Online eco farm store selling organic produce",
            "conversion_definition": "Completed checkout with payment",
            "key_journeys": "Browse produce -> add to cart -> checkout",
            "privacy_consent": "OneTrust CMP, consent-gated ad tags",
            "ownership_complexity": "Marketing owns destinations; eng owns dataLayer",
        },
    )


def test_skeleton_round_trips_through_parser():
    from app.tools.sdr_parser import parse_sdr_markdown

    parsed = parse_sdr_markdown(_skeleton())
    names = [e.name for e in parsed.events]
    assert "purchase" in names
    # Template gaps included as planned events
    assert "begin_checkout" in names
    # Intake-seeded prose present
    assert parsed.business_context and "eco farm" in parsed.business_context.lower()
    assert parsed.user_journeys and "checkout" in parsed.user_journeys.lower()


def test_synthesis_completed_sections_excludes_todo_sections():
    from app.tools.sdr_parser import parse_sdr_markdown
    from app.tools.sdr_tools import _synthesis_completed_sections

    completed = _synthesis_completed_sections(parse_sdr_markdown(_skeleton()))
    # Intake-seeded sections with no TODO are done
    assert "user_journeys" in completed
    assert "consent_and_privacy" in completed
    assert "ownership" in completed
    # business_context still has TODO KPI placeholders → not done
    assert "business_context" not in completed
    # user_properties is an empty placeholder table → not done
    assert "user_properties" not in completed


# ───────────────────────────────────────────────────────────────────────────
# Apply-change path (the previously-broken event_catalog / change_type logic)
# ───────────────────────────────────────────────────────────────────────────


def test_apply_change_event_scoped_modify_round_trips():
    from app.tools.sdr_parser import parse_sdr_markdown
    from app.tools.sdr_tools import _apply_change_to_markdown

    md = _skeleton()
    new_block = (
        "### `purchase`\n\n"
        "*Status:* `verified` | *Last verified:* `2026-05-01`\n\n"
        "**Business Purpose:** Primary revenue conversion for the eco farm store.\n\n"
        "**Triggers:**\n- Type: `datalayer_event`\n- Configuration: order confirmation page\n\n"
        "**Destinations:**\n\n- **GA4**: event name `purchase`\n"
    )
    out = _apply_change_to_markdown(md, "event_catalog.purchase", new_block, "modify")
    parsed = parse_sdr_markdown(out)
    purchase = next(e for e in parsed.events if e.name == "purchase")
    assert purchase.status == "verified"
    assert "eco farm" in (purchase.purpose or "").lower()
    # other template events survive the edit
    assert any(e.name == "begin_checkout" for e in parsed.events)


def test_apply_change_event_catalog_append_adds_new_event():
    from app.tools.sdr_parser import parse_sdr_markdown
    from app.tools.sdr_tools import _apply_change_to_markdown

    md = _skeleton()
    before = {e.name for e in parse_sdr_markdown(md).events}
    new_event = (
        "### `newsletter_signup`\n\n"
        "*Status:* `implemented` | *Last verified:* `never`\n\n"
        "**Business Purpose:** Captures email opt-ins.\n\n"
        "**Triggers:**\n- Type: `form_submit`\n- Configuration: footer form\n"
    )
    out = _apply_change_to_markdown(md, "event_catalog", new_event, "append")
    after = {e.name for e in parse_sdr_markdown(out).events}
    assert "newsletter_signup" in after
    assert before <= after  # nothing dropped


def test_apply_change_section_modify_replaces_body():
    from app.tools.sdr_parser import parse_sdr_markdown
    from app.tools.sdr_tools import _apply_change_to_markdown

    md = _skeleton()
    out = _apply_change_to_markdown(
        md, "consent_and_privacy", "Google Consent Mode v2 with default-denied EEA.", "modify"
    )
    parsed = parse_sdr_markdown(out)
    assert "consent mode v2" in (parsed.consent_and_privacy or "").lower()
    # neighbouring section intact
    assert parsed.ownership is not None


def test_apply_change_unknown_section_is_noop():
    from app.tools.sdr_tools import _apply_change_to_markdown

    md = _skeleton()
    assert _apply_change_to_markdown(md, "does_not_exist", "x", "modify") == md


# ───────────────────────────────────────────────────────────────────────────
# Source-delta computation
# ───────────────────────────────────────────────────────────────────────────


def test_source_deltas_scope_missing_to_live_events_only():
    from app.tools.sdr_parser import ParsedDestination, ParsedEvent, ParsedParameter
    from app.tools.sdr_tools import _compute_source_deltas

    current = [
        ParsedEvent(name="purchase", status="implemented"),  # live, will be missing → flagged
        ParsedEvent(name="view_item", status="planned"),  # planned, missing is normal → NOT flagged
        ParsedEvent(
            name="add_to_cart",
            status="implemented",
            destinations=[ParsedDestination(platform="ga4")],
            parameters=[ParsedParameter(name="value")],
        ),
    ]
    scanned = [
        ParsedEvent(name="sign_up", status="implemented"),  # new
        ParsedEvent(
            name="add_to_cart",
            status="implemented",
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="meta")],
            parameters=[ParsedParameter(name="value"), ParsedParameter(name="currency")],
        ),
    ]
    deltas = _compute_source_deltas(current, scanned)
    added = {e["name"] for e in deltas["added_events"]}
    missing = {e["name"] for e in deltas["removed_or_missing_from_scan"]}
    assert added == {"sign_up"}
    assert "purchase" in missing  # live event gone
    assert "view_item" not in missing  # planned event absence is not noise
    assert deltas["destination_changes"] == [{"event": "add_to_cart", "added_destinations": ["meta"]}]
    assert deltas["parameter_changes"] == [{"event": "add_to_cart", "added_parameters": ["currency"]}]
    assert deltas["proposals"]  # at least the added-event proposal


# ───────────────────────────────────────────────────────────────────────────
# Synthesis playbook is a real product surface
# ───────────────────────────────────────────────────────────────────────────


def test_synthesis_playbook_contains_schema_and_ecommerce_and_honesty():
    from app.tools.sdr_tools import _build_synthesis_playbook

    connected = {
        "connected_but_unsupported": ["meta"],
        "partial_failures": [{"source": "gtm"}],
    }
    pb = _build_synthesis_playbook(
        "BMK Eco Farms",
        "ecommerce",
        None,
        scanned_event_count=3,
        template_event_count=11,
        connected=connected,
    )
    # Canonical markdown contract present
    assert "## Event Catalog" in pb
    assert "**Business Purpose:**" in pb
    assert "*Status:*" in pb
    # Ecommerce gold-standard guidance
    assert "ViewContent" in pb
    assert "ECOMMERCE GOLD-STANDARD" in pb
    # Honesty about coverage
    assert "meta" in pb
    assert "gtm" in pb
    # Save handoff
    assert "save_sdr" in pb


# ───────────────────────────────────────────────────────────────────────────
# Destination parsing round-trip (account-id parenthetical + GOOGLE_ADS casing)
# ───────────────────────────────────────────────────────────────────────────


def test_destination_parsing_handles_ads_account_and_casing():
    from app.tools.sdr_parser import parse_sdr_markdown

    md = """## Event Catalog

### `purchase`

*Status:* `verified` | *Last verified:* `never`

**Business Purpose:** Revenue.

**Destinations:**

- **GA4**: event name `purchase`
- **GOOGLE_ADS** (`AW-123`): event name `purchase`
- **META**: event name `Purchase`
"""
    event = parse_sdr_markdown(md).events[0]
    by_platform = {d.platform: d for d in event.destinations}
    assert set(by_platform) == {"ga4", "google_ads", "meta"}
    assert by_platform["google_ads"].platform_account_id == "AW-123"


def test_generator_destination_round_trips():
    from app.tools.sdr_parser import (
        ParsedDestination,
        ParsedEvent,
        generate_sdr_markdown,
        parse_sdr_markdown,
    )

    ev = ParsedEvent(
        name="purchase",
        status="implemented",
        destinations=[
            ParsedDestination(platform="ga4", dest_event_name="purchase"),
            ParsedDestination(platform="google_ads", platform_account_id="AW-9", dest_event_name="purchase"),
        ],
    )
    md = generate_sdr_markdown(project_name="X", project_id="x", business_type="ecommerce", events=[ev])
    out = parse_sdr_markdown(md).events[0]
    platforms = {d.platform for d in out.destinations}
    # GOOGLE_ADS (the generator's own upper-cased output) must not degrade to 'custom'
    assert "google_ads" in platforms
    assert "custom" not in platforms


def test_skill_example_sdrs_round_trip_through_parser():
    """Anti-drift: every shipped skill example SDR must parse cleanly with no gaps."""
    import glob
    import os

    from app.tools.sdr_parser import compute_gaps, parse_sdr_markdown

    root = os.path.join(os.path.dirname(__file__), "..", "fluxito-skills")
    examples = glob.glob(os.path.join(root, "**", "examples", "*.md"), recursive=True)
    assert examples, "no skill example SDRs found"
    for path in examples:
        with open(path) as fh:
            parsed = parse_sdr_markdown(fh.read())
        assert parsed.events, f"{path}: no events parsed"
        assert compute_gaps(parsed) == [], f"{path}: unresolved TODO markers"
        purchase = next((e for e in parsed.events if e.name == "purchase"), None)
        if purchase:
            assert {d.platform for d in purchase.destinations} >= {"ga4"}


def test_bmk_example_sdr_is_gold_standard():
    """The shipped reference SDR must parse cleanly into projections with no gaps."""
    import os

    from app.tools.sdr_parser import compute_gaps, parse_sdr_markdown

    path = os.path.join(
        os.path.dirname(__file__), "..",
        "fluxito-skills", "fluxito-solution-design", "examples", "bmk-eco-farms-sdr.md",
    )
    if not os.path.exists(path):
        import pytest

        pytest.skip("reference SDR artifact not present")
    with open(path) as fh:
        parsed = parse_sdr_markdown(fh.read())
    assert parsed.business_type == "ecommerce"
    names = {e.name for e in parsed.events}
    # Full GA4 ecommerce funnel present
    assert {"view_item", "add_to_cart", "begin_checkout", "purchase", "refund"} <= names
    purchase = next(e for e in parsed.events if e.name == "purchase")
    assert purchase.status == "verified"
    assert {d.platform for d in purchase.destinations} == {"ga4", "google_ads", "meta"}
    assert {p.name for p in purchase.parameters} >= {"transaction_id", "value", "currency", "items"}
    # Every event is projection-ready
    assert all(e.destinations for e in parsed.events)
    # A gold-standard synthesized draft carries no unresolved TODO markers
    assert compute_gaps(parsed) == []
