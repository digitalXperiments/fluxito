"""File-read tests for the consolidated /settings shell.

These tests are pure file reads (no HTTP, no DB) — same style as
tests/test_project_settings_template.py and tests/test_admin_updates_template.py.
"""

from pathlib import Path

INDEX_TEMPLATE = Path("app/templates/settings/index.html")
BASE_TEMPLATE = Path("app/templates/base.html")


# ---------------------------------------------------------------------------
# settings/index.html — role-gated regions
# ---------------------------------------------------------------------------


def test_account_tab_always_present():
    source = INDEX_TEMPLATE.read_text()
    assert 'data-tab="account"' in source
    assert "/profile?embed=1" in source


def test_project_tab_gated_on_active_project_slug():
    source = INDEX_TEMPLATE.read_text()
    # The project tab must be inside a Jinja conditional on active_project_slug
    assert "{% if active_project_slug %}" in source
    assert 'data-tab="project"' in source


def test_workspace_tabs_gated_on_is_install_admin():
    source = INDEX_TEMPLATE.read_text()
    assert "{% if is_install_admin %}" in source
    assert 'data-tab="integrations"' in source
    assert 'data-tab="system"' in source
    assert "/settings/integrations?embed=1" in source
    assert "/settings/system?embed=1" in source


def test_platform_tab_gated_on_is_superadmin():
    source = INDEX_TEMPLATE.read_text()
    assert "{% if is_superadmin %}" in source
    assert 'data-tab="platform"' in source
    assert "/admin?embed=1" in source


def test_all_five_data_tab_values_present():
    source = INDEX_TEMPLATE.read_text()
    for tab in ("account", "project", "integrations", "system", "platform"):
        assert f'data-tab="{tab}"' in source, f"Missing data-tab={tab!r}"


def test_iframe_with_settings_frame_class_present():
    source = INDEX_TEMPLATE.read_text()
    assert 'class="settings-frame"' in source
    assert "<iframe" in source


# ---------------------------------------------------------------------------
# base.html — embed guard
# ---------------------------------------------------------------------------


def test_base_html_body_class_has_embed_guard():
    source = BASE_TEMPLATE.read_text()
    # Chrome is suppressed in embed mode via the `embed` context var (computed
    # defensively in templating.render() so templates never touch
    # request.query_params, which a minimal Request scope may lack).
    assert "not embed" in source


def test_base_html_sidebar_wrapped_in_embed_guard():
    source = BASE_TEMPLATE.read_text()
    # The embed guard must appear before the sidebar and the mobile topbar
    guard_idx = source.index("{% if not embed %}")
    sidebar_idx = source.index('<aside class="sidebar">')
    mobile_idx = source.index('<div class="mobile-topbar">')
    assert guard_idx < sidebar_idx
    assert guard_idx < mobile_idx


def test_templating_computes_embed_flag():
    # render() must inject an `embed` flag so base.html never reads request.query_params.
    src = Path("app/templating.py").read_text()
    assert '"embed"' in src and "query_params.get(" in src
