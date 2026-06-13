# tests/api/test_tracking_plan_routes.py
import pytest

# These tests exercise the router logic against the test DB. They monkeypatch
# auth + active-project resolution so we don't need a real session cookie.


@pytest.fixture
async def client(db_engine, db_session_factory, monkeypatch):
    import uuid

    from httpx import ASGITransport, AsyncClient

    import app.app_state as app_state

    # Point the app's session factory at the test engine
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    # Seed a project + user, stub auth to return them
    from app.models.project import Project, ProjectMember
    from app.models.user import User

    async with db_session_factory() as s:
        user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com")
        s.add(user)
        await s.flush()
        project = Project(name="P", slug=f"p-{uuid.uuid4().hex[:8]}", owner_id=user.id)
        s.add(project)
        await s.flush()
        # Make the user an owner member using the real ProjectMember model
        s.add(
            ProjectMember(
                project_id=project.id,
                user_id=user.id,
                role="owner",
                is_active=True,
            )
        )
        await s.commit()
        uid, pid = str(user.id), str(project.id)

    import app.api.tracking_plan_routes as tpr

    async def fake_resolve_ctx(request):
        from types import SimpleNamespace

        return SimpleNamespace(user_id=uid, email="t@example.com")

    async def fake_ensure_project(request, user_id):
        return pid

    monkeypatch.setattr(tpr, "_resolve_user_ctx", fake_resolve_ctx)
    monkeypatch.setattr(tpr, "ensure_active_project", fake_ensure_project)

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._pid = pid  # type: ignore[attr-defined]
        yield c


@pytest.mark.anyio
async def test_get_plan_and_create_event(client):
    pid = client._pid
    # Empty plan auto-creates
    r = await client.get(f"/api/projects/{pid}/tracking-plan")
    assert r.status_code == 200
    assert r.json()["events"] == []

    # Create an event via the action endpoint
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "purchase"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = await client.get(f"/api/projects/{pid}/tracking-plan")
    assert [e["name"] for e in r.json()["events"]] == ["purchase"]


@pytest.mark.anyio
async def test_publish_creates_version(client):
    pid = client._pid
    await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "purchase"}},
    )
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "publish", "params": {"changelog": "v1"}},
    )
    assert r.json()["ok"] is True
    versions = (await client.get(f"/api/projects/{pid}/tracking-plan/versions")).json()
    assert versions["versions"][0]["version_number"] == "1.0"


@pytest.mark.anyio
async def test_page_renders(client):
    r = await client.get("/tracking-plan")
    assert r.status_code == 200
    assert "Tracking Plan" in r.text
    assert 'id="tp-app"' in r.text
    assert "/static/js/tracking_plan.js" in r.text


@pytest.mark.anyio
async def test_list_branches_returns_main(client):
    """GET .../branches always returns at least the main branch."""
    pid = client._pid
    r = await client.get(f"/api/projects/{pid}/tracking-plan/branches")
    assert r.status_code == 200
    data = r.json()
    assert "branches" in data
    assert len(data["branches"]) >= 1
    assert data["branches"][0]["is_main"] is True
    assert data["branches"][0]["name"] == "main"


@pytest.mark.anyio
async def test_branch_aware_get_plan(client):
    """POST action/create_branch, then GET ?branch=<name> reflects branch edits
    while GET ?branch=main (or no param) does not."""
    pid = client._pid

    # Create a feature branch via the action endpoint
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_branch", "params": {"name": "feat/http-test"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Add an event on the feature branch via action (branch param in params)
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={
            "action": "create_event",
            "params": {"name": "branch_only_event", "branch": "feat/http-test"},
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # GET ?branch=feat/http-test should contain the new event
    r = await client.get(f"/api/projects/{pid}/tracking-plan?branch=feat/http-test")
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["events"]]
    assert "branch_only_event" in names

    # GET ?branch=main (default) should NOT contain the new event
    r = await client.get(f"/api/projects/{pid}/tracking-plan?branch=main")
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["events"]]
    assert "branch_only_event" not in names


@pytest.mark.anyio
async def test_version_snapshot_rejects_other_projects_version(client, db_session_factory):
    """Cross-tenant guard: a version belonging to project B must 404 when
    requested through project A's URL."""
    from app.services.tracking_plan import (
        create_event,
        get_main_branch,
        get_or_create_plan,
        publish_branch,
    )
    from tests.services.tracking_plan.test_models import _make_project_and_user

    # Build a second project (B) with its own plan and publish a version in it.
    async with db_session_factory() as s:
        other_pid, other_uid = await _make_project_and_user(s)
        plan = await get_or_create_plan(s, project_id=other_pid, user_id=other_uid)
        branch = await get_main_branch(s, plan)
        await create_event(s, branch, name="secret_event")
        version = await publish_branch(s, plan, branch, user_id=other_uid)
        await s.commit()
        other_version_id = str(version.id)

    # Request project B's version through project A's URL — must be 404.
    pid = client._pid
    r = await client.get(f"/api/projects/{pid}/tracking-plan/versions/{other_version_id}")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_activity_endpoint_lists_writes(client):
    pid = client._pid
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "signup"}},
    )
    assert r.status_code == 200
    a = await client.get(f"/api/projects/{pid}/tracking-plan/activity")
    assert a.status_code == 200
    items = a.json()["activity"]
    assert any(x["action"] == "create_event" and x["entity_type"] == "event" for x in items)


@pytest.mark.anyio
async def test_activity_endpoint_filters_by_entity(client):
    pid = client._pid
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "signup2"}},
    )
    eid = r.json()["id"]
    a = await client.get(
        f"/api/projects/{pid}/tracking-plan/activity",
        params={"entity_type": "event", "entity_id": eid},
    )
    assert a.status_code == 200
    items = a.json()["activity"]
    assert items and all(x["entity_id"] == eid for x in items)


@pytest.mark.anyio
async def test_members_endpoint_returns_seeded_owner(client):
    pid = client._pid
    r = await client.get(f"/api/projects/{pid}/members")
    assert r.status_code == 200
    members = r.json()["members"]
    assert len(members) >= 1
    assert all({"id", "display_name", "initials"} <= set(m) for m in members)
