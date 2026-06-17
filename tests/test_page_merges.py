"""File-read tests for the Phase D page merges.

Pure file reads — no HTTP, no DB. Mirrors the style of
tests/test_settings_consolidation.py.
"""

from pathlib import Path

CONTEXT_TEMPLATE = Path("app/templates/context.html")
DASHBOARDS_HUB_TEMPLATE = Path("app/templates/dashboards_hub.html")
KNOWLEDGE_ROUTES = Path("app/api/knowledge_routes.py")
DASHBOARD_ROUTES = Path("app/api/dashboard_routes.py")
TEMPLATE_ROUTES = Path("app/api/template_routes.py")
AUDIT_ROUTES = Path("app/api/audit_routes.py")


# ---------------------------------------------------------------------------
# context.html
# ---------------------------------------------------------------------------


def test_context_has_kpis_tab_button():
    source = CONTEXT_TEMPLATE.read_text()
    assert 'data-tab="kpis"' in source


def test_context_has_business_tab_button():
    source = CONTEXT_TEMPLATE.read_text()
    assert 'data-tab="business"' in source


def test_context_has_kpi_library_embed_iframe():
    source = CONTEXT_TEMPLATE.read_text()
    assert "/kpi-library?embed=1" in source


def test_context_has_business_context_embed_iframe():
    source = CONTEXT_TEMPLATE.read_text()
    assert "/business-context?embed=1" in source


def test_context_iframe_present():
    source = CONTEXT_TEMPLATE.read_text()
    assert "<iframe" in source


def test_context_tab_buttons_both_present():
    source = CONTEXT_TEMPLATE.read_text()
    assert "KPIs" in source
    assert "Business context" in source


def test_context_selects_business_on_hash():
    source = CONTEXT_TEMPLATE.read_text()
    # The JS must check for the #business hash to select the business tab.
    assert "#business" in source
    assert "'business'" in source


def test_context_active_is_context():
    source = CONTEXT_TEMPLATE.read_text()
    assert "active = 'context'" in source


# ---------------------------------------------------------------------------
# dashboards_hub.html
# ---------------------------------------------------------------------------


def test_dashboards_hub_has_dashboards_tab_button():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert 'data-tab="dashboards"' in source


def test_dashboards_hub_has_gallery_tab_button():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert 'data-tab="gallery"' in source


def test_dashboards_hub_has_live_dashboards_embed_iframe():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert "/live-dashboards?embed=1" in source


def test_dashboards_hub_has_templates_embed_iframe():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert "/templates?embed=1" in source


def test_dashboards_hub_iframe_present():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert "<iframe" in source


def test_dashboards_hub_tab_labels():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert "Your dashboards" in source
    assert "Gallery" in source


def test_dashboards_hub_active_is_dashboards():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    assert "active = 'dashboards'" in source


def test_dashboards_hub_gallery_view_param():
    source = DASHBOARDS_HUB_TEMPLATE.read_text()
    # The JS must use ?view= param to select the gallery tab on load.
    assert "'view'" in source or '"view"' in source


# ---------------------------------------------------------------------------
# Route redirects — knowledge_routes.py
# ---------------------------------------------------------------------------


def test_kpi_library_redirect_to_context():
    source = KNOWLEDGE_ROUTES.read_text()
    # kpi_library_page must redirect to /context when not embed.
    assert 'RedirectResponse("/context"' in source


def test_kpi_library_redirect_guarded_by_embed():
    source = KNOWLEDGE_ROUTES.read_text()
    # The embed guard must be present in the route.
    assert 'request.query_params.get("embed")' in source


def test_business_context_redirect_to_context_hash():
    source = KNOWLEDGE_ROUTES.read_text()
    # business_context_page must redirect to /context#business when not embed.
    assert 'RedirectResponse("/context#business"' in source


def test_context_route_defined():
    source = KNOWLEDGE_ROUTES.read_text()
    assert '@router.get("/context")' in source


# ---------------------------------------------------------------------------
# Route redirects — dashboard_routes.py
# ---------------------------------------------------------------------------


def test_dashboards_hub_route_defined():
    source = DASHBOARD_ROUTES.read_text()
    assert '@router.get("/dashboards"' in source


def test_live_dashboards_redirect_to_dashboards():
    source = DASHBOARD_ROUTES.read_text()
    assert 'RedirectResponse("/dashboards"' in source


def test_live_dashboards_redirect_guarded_by_embed():
    source = DASHBOARD_ROUTES.read_text()
    assert 'request.query_params.get("embed")' in source


# ---------------------------------------------------------------------------
# Route redirects — template_routes.py
# ---------------------------------------------------------------------------


def test_templates_redirect_to_dashboards_gallery():
    source = TEMPLATE_ROUTES.read_text()
    assert 'RedirectResponse("/dashboards?view=gallery"' in source


def test_templates_redirect_guarded_by_embed():
    source = TEMPLATE_ROUTES.read_text()
    assert 'request.query_params.get("embed")' in source


# ---------------------------------------------------------------------------
# Sub-routes untouched (no redirect added to slug routes)
# ---------------------------------------------------------------------------


def test_live_dashboards_slug_route_has_no_embed_redirect():
    source = DASHBOARD_ROUTES.read_text()
    # The /live-dashboards/{slug} handler must NOT contain a redirect to /dashboards.
    # We verify by checking that the redirect only appears once (in live_dashboards_hub).
    assert source.count('RedirectResponse("/dashboards"') == 1


def test_templates_slug_route_has_no_embed_redirect():
    source = TEMPLATE_ROUTES.read_text()
    # The redirect to /dashboards?view=gallery must only appear once (in templates_page).
    assert source.count('RedirectResponse("/dashboards?view=gallery"') == 1


# ---------------------------------------------------------------------------
# Route redirects — audit_routes.py (/activity-log → Settings)
# ---------------------------------------------------------------------------


def test_activity_log_embed_guard_present():
    source = AUDIT_ROUTES.read_text()
    assert 'request.query_params.get("embed")' in source


def test_activity_log_redirects_to_settings_activity():
    source = AUDIT_ROUTES.read_text()
    assert 'RedirectResponse("/settings?tab=activity"' in source
