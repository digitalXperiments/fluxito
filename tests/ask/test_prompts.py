from app.ask.prompts import build_system_prompt


def test_prompt_includes_project_clarify_and_readonly():
    p = build_system_prompt(project_name="Acme", connected=["GA4", "GTM"], role="editor")
    assert "Ask Fluxito" in p
    assert "Acme" in p
    assert "GA4" in p and "GTM" in p
    assert "<clarification>" in p and "</clarification>" in p
    # read-only guarantee must be explicit
    assert "read-only" in p.lower() or "cannot modify" in p.lower()


def test_prompt_handles_no_connections():
    p = build_system_prompt(project_name="Acme", connected=[], role="viewer")
    assert "no data sources" in p.lower() or "none connected" in p.lower()


def test_implement_section_addendum_mentions_propose_change_and_drafts():
    p = build_system_prompt(
        project_name="Acme",
        connected=["GTM"],
        role="editor",
        page_context={"section": "implement", "route": "/implement"},
    )
    assert "<section>" in p
    assert "tagmanager_write" in p
    assert "propose_change" in p
    # write-enabled section must still forbid claiming a change is live pre-approval
    assert "draft" in p.lower()
    assert "approve" in p.lower()
    # the read-only tools block must be replaced by the "propose via draft" language
    assert "read-only access" not in p.lower()


def test_report_section_addendum_mentions_dashboards():
    p = build_system_prompt(
        project_name="Acme",
        connected=["GA4"],
        role="editor",
        page_context={"section": "report"},
    )
    assert "<section>" in p
    assert "dashboard_read" in p
    # report is not write-enabled — read-only guarantee stays
    assert "read-only access" in p.lower()


def test_other_section_gets_one_line_orientation():
    p = build_system_prompt(
        project_name="Acme",
        connected=["GA4"],
        role="editor",
        page_context={"section": "plan"},
    )
    assert "<section>" in p
    assert "tracking plan" in p.lower()
    assert "read-only access" in p.lower()


def test_no_page_context_has_no_section_block():
    p = build_system_prompt(project_name="Acme", connected=["GA4"], role="editor")
    assert "<section>" not in p
    assert "read-only access" in p.lower()
