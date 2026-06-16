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
