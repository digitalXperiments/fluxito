# tests/test_rbac_permissions.py
"""Unit tests for the RBAC permission vocabulary and resolver."""
import pytest
import app.app_state as app_state


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


def test_domain_tools_map_covers_dispatchers():
    from app.auth.permissions import DOMAIN_TOOLS
    assert DOMAIN_TOOLS["tagmanager"]["read"] == {"tagmanager_read"}
    assert "tagmanager_write" in DOMAIN_TOOLS["tagmanager"]["write"]
    assert "analytics_read" in DOMAIN_TOOLS["analytics"]["read"]


def test_always_on_tools():
    from app.auth.permissions import ALWAYS_ON_TOOLS
    assert {"get_session_context", "list_my_projects", "set_active_project"} <= ALWAYS_ON_TOOLS


def test_full_permissions_allow_everything():
    from app.auth.permissions import EffectivePermissions
    eff = EffectivePermissions(full=True)
    assert eff.allows_tool("marketing_write") is True
    assert eff.allows_provider("amplitude") is True


def test_scoped_permissions_allow_only_granted():
    from app.auth.permissions import EffectivePermissions
    eff = EffectivePermissions(
        full=False,
        tools={"tagmanager": {"read", "write"}, "analytics": {"read"}},
        providers={"ga4", "gtm"},
    )
    assert eff.allows_tool("tagmanager_write") is True
    assert eff.allows_tool("analytics_read") is True
    assert eff.allows_tool("analytics_write") is False
    assert eff.allows_tool("marketing_read") is False
    assert eff.allows_tool("set_active_project") is True
    assert eff.allows_provider("ga4") is True
    assert eff.allows_provider("amplitude") is False


def test_tracking_plan_action_mapping():
    from app.auth.permissions import EffectivePermissions
    eff = EffectivePermissions(full=False, tools={"tracking_plan": {"read"}})
    assert eff.allows_tool("tracking_plan", action="generate") is True
    assert eff.allows_tool("tracking_plan", action="save") is False
    assert eff.allows_tool("tracking_plan", action="mystery") is False


def test_scripting_is_advanced_gate():
    from app.auth.permissions import EffectivePermissions
    denied = EffectivePermissions(full=False, advanced=set())
    assert denied.allows_tool("run_script") is False
    granted = EffectivePermissions(full=False, advanced={"scripting"})
    assert granted.allows_tool("run_script") is True


def test_normalize_permissions_write_implies_read():
    from app.auth.permissions import normalize_permissions
    out = normalize_permissions({"tools": {"tagmanager": ["write"]}, "providers": ["ga4"]})
    assert set(out["tools"]["tagmanager"]) == {"read", "write"}
    assert out["providers"] == ["ga4"]


def test_normalize_rejects_unknown_domain_and_provider():
    from app.auth.permissions import normalize_permissions, PermissionValidationError
    with pytest.raises(PermissionValidationError):
        normalize_permissions({"tools": {"nope": ["read"]}})
    with pytest.raises(PermissionValidationError):
        normalize_permissions({"providers": ["myspace"]})


def test_union_roles_builds_effective():
    from app.auth.permissions import _union_role_docs
    eff = _union_role_docs([
        {"tools": {"seo": ["read"]}, "providers": ["gsc"]},
        {"tools": {"dashboards": ["read", "write"]}, "providers": ["ga4"], "advanced": {"scripting": True}},
    ])
    assert eff.full is False
    assert eff.tools["seo"] == {"read"}
    assert eff.tools["dashboards"] == {"read", "write"}
    assert eff.providers == {"gsc", "ga4"}
    assert "scripting" in eff.advanced


@pytest.mark.asyncio
async def test_resolver_owner_is_full(_patch_db, db_session_factory, monkeypatch):
    from unittest.mock import AsyncMock
    import app.auth.permissions as perms
    from app.models.user import User
    from app.models.project import Project, ProjectMember

    async with db_session_factory() as db:
        u = User(email="own@example.com")
        db.add(u); await db.flush()
        p = Project(name="P", slug="rp1", owner_id=u.id, rbac_enabled=True)
        db.add(p); await db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner"))
        await db.flush()
        uid, pid = str(u.id), str(p.id)
        await db.commit()

    monkeypatch.setattr(perms, "_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(perms, "_cache_set", AsyncMock(return_value=None))
    eff = await perms.resolve_effective_permissions(uid, pid)
    assert eff.full is True


@pytest.mark.asyncio
async def test_resolver_member_rbac_off_is_full(_patch_db, db_session_factory, monkeypatch):
    from unittest.mock import AsyncMock
    import app.auth.permissions as perms
    from app.models.user import User
    from app.models.project import Project, ProjectMember

    async with db_session_factory() as db:
        u = User(email="mem@example.com")
        db.add(u); await db.flush()
        p = Project(name="P", slug="rp2", owner_id=u.id, rbac_enabled=False)
        db.add(p); await db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id, role="member"))
        await db.flush()
        uid, pid = str(u.id), str(p.id)
        await db.commit()

    monkeypatch.setattr(perms, "_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(perms, "_cache_set", AsyncMock(return_value=None))
    eff = await perms.resolve_effective_permissions(uid, pid)
    assert eff.full is True


@pytest.mark.asyncio
async def test_resolver_member_unions_assigned_roles(_patch_db, db_session_factory, monkeypatch):
    from unittest.mock import AsyncMock
    import app.auth.permissions as perms
    from app.models.user import User
    from app.models.project import Project, ProjectMember
    from app.models.role import Role, MemberRole

    async with db_session_factory() as db:
        u = User(email="scoped@example.com")
        db.add(u); await db.flush()
        p = Project(name="P", slug="rp3", owner_id=u.id, rbac_enabled=True)
        db.add(p); await db.flush()
        pm = ProjectMember(project_id=p.id, user_id=u.id, role="member")
        db.add(pm); await db.flush()
        r1 = Role(project_id=p.id, name="SEO", permissions={"tools": {"seo": ["read"]}, "providers": ["gsc"]}, created_by=u.id)
        r2 = Role(project_id=p.id, name="View", permissions={"tools": {"dashboards": ["read"]}, "providers": ["ga4"]}, created_by=u.id)
        db.add_all([r1, r2]); await db.flush()
        db.add_all([
            MemberRole(project_member_id=pm.id, role_id=r1.id),
            MemberRole(project_member_id=pm.id, role_id=r2.id),
        ])
        await db.flush()
        uid, pid = str(u.id), str(p.id)
        await db.commit()

    monkeypatch.setattr(perms, "_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(perms, "_cache_set", AsyncMock(return_value=None))
    eff = await perms.resolve_effective_permissions(uid, pid)
    assert eff.full is False
    assert eff.tools["seo"] == {"read"}
    assert eff.tools["dashboards"] == {"read"}
    assert eff.providers == {"gsc", "ga4"}
