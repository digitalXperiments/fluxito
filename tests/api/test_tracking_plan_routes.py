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
