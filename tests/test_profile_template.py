from pathlib import Path

from jinja2 import Environment

PROFILE_TEMPLATE = Path("app/templates/profile.html")


def test_profile_template_syntax_valid():
    source = PROFILE_TEMPLATE.read_text()
    Environment().parse(source)


def test_profile_tabs_structure_and_order():
    source = PROFILE_TEMPLATE.read_text()
    assert 'id="pfTabs"' in source
    assert 'data-tab="general"' in source
    assert 'data-tab="preferences"' in source
    assert 'data-tab="tokens"' in source
    assert 'data-tab="activity"' in source
    assert 'data-tab="ai"' not in source

    # Tab contents carry matching data-pf-tab markers
    assert 'data-pf-tab="general"' in source
    assert 'data-pf-tab="preferences"' in source
    assert 'data-pf-tab="tokens"' in source
    assert 'data-pf-tab="activity"' in source
    assert 'data-pf-tab="ai"' not in source


def test_profile_header_matches_settings_style():
    source = PROFILE_TEMPLATE.read_text()
    assert 'class="ps-eyebrow"' in source
    assert 'class="ps-head"' in source
    assert 'class="ps-mark"' in source
    assert 'id="profileAvatar"' in source
    assert 'id="profileDisplayName"' in source


def test_profile_preserves_required_hooks_and_forms():
    source = PROFILE_TEMPLATE.read_text()
    for hook in (
        "profileForm",
        "displayName",
        "emailField",
        "newPasswordField",
        "saveProfileBtn",
        "danger",
        "accountActions",
        "pfRoleGrid",
        "pfMonitorChips",
        "pfClientGrid",
        "pfMcpUrl",
        "pfCopyMcpUrl",
        "mcpTokensCard",
        "mcpTokenForm",
        "mcpTokenName",
        "mcpTokenExpiry",
        "mcpCreateBtn",
        "mcpPatReveal",
        "mcpPatPlain",
        "mcpPatSnippet",
        "mcpPatList",
        "usageModal",
        "usageModalBody",
    ):
        assert f'id="{hook}"' in source, f"Missing required hook: {hook}"


def test_profile_js_functions_defined():
    source = PROFILE_TEMPLATE.read_text()
    for fn in (
        "Fluxito._saveProfile",
        "Fluxito._openUsageModal",
        "Fluxito._deactivateAccount",
        "Fluxito._reactivateAccount",
        "Fluxito._deleteAccount",
        "Fluxito._savePrefs",
        "Fluxito._createMcpPat",
        "Fluxito._addPatToList",
        "Fluxito._revokeMcpPat",
    ):
        assert fn in source, f"Missing JS function: {fn}"
