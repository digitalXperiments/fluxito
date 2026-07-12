from pathlib import Path

SETTINGS_TEMPLATE = Path("app/templates/projects/settings.html")


def test_settings_tabs_are_text_only_and_ordered():
    source = SETTINGS_TEMPLATE.read_text()
    # The revamp replaced the tab bar with a single editorial scroll; sections
    # keep stable ids and appear in reading order.
    assert 'class="ps-tabs"' not in source
    assert "ps-tab-n" not in source
    order = [
        'id="general"',
        'id="members"',
        'id="roles"',
        'id="connections"',
        'id="limits"',
        'id="notifications"',
    ]
    positions = [source.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_role_editor_uses_guided_sections_and_preserves_hooks():
    source = SETTINGS_TEMPLATE.read_text()

    assert source.count('class="ps-role-editor-section"') == 4
    for hook in (
        "roleForm",
        "roleFormTitle",
        "roleFormState",
        "toolGrid",
        "provSelect",
        "roleSaveBtn",
        "roleCancelBtn",
        "roleMsg",
    ):
        assert f'id="{hook}"' in source


def test_user_facing_role_copy_uses_users_label():
    source = SETTINGS_TEMPLATE.read_text()

    assert "Members tab" not in source
    assert "Manage members" not in source
    assert "Manage users" in source
    assert "your users need" in source


def test_user_roles_prioritizes_custom_roles_and_collapses_reference():
    source = SETTINGS_TEMPLATE.read_text()
    roles_panel = source[source.index('id="roles"') : source.index("{# ── Member roles popover")]

    assert roles_panel.index('id="rolesListCard"') < roles_panel.index('class="ps-built-in-roles"')
    assert '<details class="ps-built-in-roles">' in roles_panel
    assert '<summary class="ps-built-in-summary">' in roles_panel
    assert 'id="createRoleBtn"' in roles_panel


def test_role_editor_lives_in_accessible_drawer():
    source = SETTINGS_TEMPLATE.read_text()

    drawer_start = source.index('id="roleDrawer"')
    drawer_end = source.index("{# ── Member roles popover")
    drawer = source[drawer_start:drawer_end]

    assert 'id="roleDrawerBackdrop"' in source[:drawer_start]
    assert 'role="dialog"' in drawer
    assert 'aria-modal="true"' in drawer
    assert 'aria-labelledby="roleFormTitle"' in drawer
    assert "inert" in drawer
    assert 'id="roleDrawerClose"' in drawer
    assert 'id="roleForm"' in drawer


def test_role_drawer_interaction_hooks_are_present():
    source = SETTINGS_TEMPLATE.read_text()

    assert "function openRoleDrawer" in source
    assert "function closeRoleDrawer" in source
    assert "openRoleDrawer('create'" in source
    assert "openRoleDrawer(\\'edit\\'" in source
    assert "roleDrawerBackdrop" in source
    assert "roleDrawerClose" in source
