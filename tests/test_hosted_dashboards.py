"""Hosted web dashboard contract: validate, deploy, bind, isolated origin."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

import app.app_state as app_state
from app.auth.mcp_session_manager import UserContext
from app.dashboards.artifact import ArtifactError, validate_artifact
from app.dashboards.authoring_guide import AUTHORING_GUIDE, authoring_guide_payload
from app.dashboards.connections import bind_requirements
from app.dashboards.data_plane import run_alias_query
from app.models.dashboard import Dashboard
from app.models.user import User
from app.tools.dashboard_tools import register_dashboard_tools


def _manifest(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "kind": "web",
        "title": "GA4 last 30 days",
        "entrypoint": "index.html",
        "connections": [{"alias": "ga4", "type": "ga4", "required": True}],
    }
    data.update(overrides)
    return data


def _index_html() -> str:
    return (
        "<!doctype html><html><body>"
        "<h1>GA4 last 30 days</h1>"
        "<script src='./app.js'></script>"
        "</body></html>"
    )


def _app_js() -> str:
    return (
        "async function main(){\n"
        "  const data = await fluxito.query('ga4', 'run_report', {metrics: ['sessions']});\n"
        "  if (data.error) { console.error(data.message); return; }\n"
        "  fluxito.rows(data);\n"
        "}\n"
        "main();\n"
    )


def _files(**extra) -> dict[str, str]:
    files = {
        "manifest.json": json.dumps(_manifest()),
        "index.html": _index_html(),
        "app.js": _app_js(),
    }
    files.update(extra)
    return files


class _FakeTool:
    def __init__(self, fn):
        self._fn = fn

    async def run(self, call_args, *_a, **_k):
        return await self._fn(call_args)


def test_validate_happy_path():
    art = validate_artifact(_files())
    assert art.manifest.entrypoint == "index.html"
    assert art.manifest.connections[0].alias == "ga4"
    assert art.digest


def test_validate_rejects_secrets():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(_files(**{"app.js": _app_js() + "\nAPI_KEY = 'sk-live-supersecrettoken'\n"}))
    assert any("secret" in e.lower() or "api" in e.lower() for e in exc.value.errors)


def test_validate_rejects_env_file():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), ".env": "DATABASE_URL=postgres://u:p@h/db"})
    assert any(".env" in e or "forbidden" in e.lower() for e in exc.value.errors)


def test_validate_rejects_private_key():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), "notes.md": "-----BEGIN PRIVATE KEY-----\nMIIE\n"})
    assert any("secret" in e.lower() or "pem" in e.lower() for e in exc.value.errors)


def test_validate_rejects_missing_entrypoint():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({"manifest.json": json.dumps(_manifest())})
    assert any("entrypoint" in e for e in exc.value.errors)


def test_validate_rejects_jsx_source():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), "App.jsx": "export default function App(){return null}"})
    assert any("jsx" in e.lower() or "compile" in e.lower() for e in exc.value.errors)


def test_validate_rejects_python_and_streamlit():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), "app.py": "import streamlit as st\nst.title('x')\n"})
    assert any(".py" in e.lower() or "compile" in e.lower() for e in exc.value.errors)


def test_validate_rejects_remote_script():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(
            {
                **_files(),
                "index.html": '<html><script src="https://evil.example/x.js"></script></html>',
            }
        )
    assert any("remote" in e.lower() for e in exc.value.errors)


def test_validate_rejects_path_traversal():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), "../evil.js": "alert(1)"})
    assert any("traversal" in e.lower() or ".." in e for e in exc.value.errors)


def test_validate_warns_without_fluxito_query():
    art = validate_artifact(
        {
            "manifest.json": json.dumps(_manifest()),
            "index.html": "<!doctype html><html><body>static</body></html>",
        }
    )
    assert any("fluxito.query" in w for w in art.warnings)


def test_authoring_guide_is_the_contract():
    payload = authoring_guide_payload()
    guide = payload["guide"]
    assert payload["kind"] == "web"
    assert "fluxito.query" in guide
    assert "manifest.json" in guide
    assert "index.html" in guide
    assert "does not compile" in guide.lower() or "production" in guide.lower()
    assert "do not put credentials" in guide.lower() or "never put secrets" in guide.lower()
    assert "validate_dashboard_artifact" in guide
    assert "deploy_dashboard" in guide
    assert "bind_dashboard" in guide
    assert "unregistered" in guide.lower() or "do not call" in guide.lower()
    assert "streamlit" in guide.lower()  # forbidden, called out
    assert AUTHORING_GUIDE
    assert "ga4" in payload["connection_types"]
    assert "recipes" in payload
    assert payload["recipes"]["ga4"]["action"] == "run_report"
    assert "metrics" in payload["recipes"]["ga4"]["send"]
    assert "property_id" in payload["recipes"]["ga4"]["injected"]
    from app.dashboards.query_recipes import assert_recipes_cover_types

    assert assert_recipes_cover_types() == []


def test_requirements_do_not_pin_streamlit():
    text = Path(__file__).resolve().parents[1].joinpath("requirements.txt").read_text(encoding="utf-8")
    assert "streamlit" not in text


def test_write_artifact_injects_sdk(tmp_path):
    from app.dashboards.runtime import write_artifact

    art = validate_artifact(_files())
    write_artifact(
        tmp_path / "d1",
        art,
        bindings=[{"alias": "ga4", "type": "ga4", "status": "bound"}],
        dashboard_id="d1",
        slug="x",
    )
    helper = (tmp_path / "d1" / "fluxito.js").read_text()
    assert "fluxito.query" in helper or "function query" in helper
    assert "DATABASE_URL" not in helper
    assert "TOKEN_ENCRYPTION_KEY" not in helper
    html = (tmp_path / "d1" / "index.html").read_text()
    assert "/fluxito.js" in html


def test_bind_requirements_marks_missing_and_bound():
    from app.dashboards.artifact import ConnectionRequirement

    reqs = [
        ConnectionRequirement("ga4", "ga4"),
        ConnectionRequirement("ads", "google_ads"),
    ]
    available = [
        {
            "type": "ga4",
            "label": "GA4 (123)",
            "connection_id": "c1",
            "resource_key": "property_id",
            "resource_value": "123",
            "status": "active",
        }
    ]
    bindings = bind_requirements(reqs, available)
    assert bindings[0]["status"] == "bound"
    assert bindings[0]["resource_value"] == "123"
    assert bindings[1]["status"] == "missing"


def test_embed_token_roundtrip_and_expiry():
    from app.dashboards.embed_token import mint_embed_token, verify_embed_token

    token, ttl = mint_embed_token(
        slug="abc",
        dashboard_id="11111111-1111-1111-1111-111111111111",
        viewer_id="user-1",
        aliases=["ga4"],
    )
    assert ttl >= 60
    payload = verify_embed_token(token)
    assert payload is not None
    assert payload["slug"] == "abc"
    assert verify_embed_token("nope") is None
    assert verify_embed_token(token[:-2] + "xx") is None


def test_origins_are_isolated_by_default():
    from app.dashboards.origin import app_origin, dashboard_origin, origins_are_isolated

    assert app_origin() != dashboard_origin()
    assert origins_are_isolated()


@pytest.mark.asyncio
async def test_data_plane_injects_bound_resource_without_db():
    class _Dash:
        id = uuid.uuid4()
        connection_bindings = [
            {
                "alias": "ga4",
                "type": "ga4",
                "status": "bound",
                "tool": "analytics_read",
                "resource_key": "property_id",
                "resource_value": "279951751",
            }
        ]

    seen: dict = {}

    async def _ga4(call_args):
        seen.update(call_args)
        return {"rows": [{"date": "2026-01-01", "sessions": "10"}]}

    class _TM:
        _tools = {"analytics_read": _FakeTool(_ga4)}
        _legacy_tools = {}

    from unittest.mock import AsyncMock, patch

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    with patch("app.auth.mcp_session_manager.build_refresh_context", new=AsyncMock(return_value=_Ctx())):
        result = await run_alias_query(
            _Dash(),
            alias="ga4",
            action="run_report",
            params={"metrics": ["sessions"]},
            tool_manager=_TM(),
        )
    assert result.get("error") is not True
    assert seen["property_id"] == "279951751"
    assert seen["action"] == "run_report"


@pytest.mark.asyncio
async def test_data_plane_rejects_unbound_and_unknown_alias():
    class _Dash:
        id = uuid.uuid4()
        connection_bindings = [{"alias": "ga4", "type": "ga4", "status": "missing"}]

    missing = await run_alias_query(
        _Dash(), alias="ga4", action="run_report", params={}, tool_manager=object()
    )
    assert missing["error"] is True
    assert missing["error_type"] == "unbound"

    unknown = await run_alias_query(
        _Dash(), alias="ads", action="run_report", params={}, tool_manager=object()
    )
    assert unknown["error_type"] == "unknown_alias"


def _build_server() -> FastMCP:
    server = FastMCP(name="hosted-dashboard-tools-test")
    register_dashboard_tools(server)
    return server


def _tool(server: FastMCP, name: str):
    return server._tool_manager._tools[name].fn


async def _make_user(db_session_factory) -> uuid.UUID:
    async with db_session_factory() as db:
        u = User(email=f"hosted-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


@contextmanager
def _user_ctx(uid: uuid.UUID):
    ctx = UserContext(user_id=str(uid), email="owner@example.com", display_name="Owner")
    user_tok = app_state.current_user_ctx.set(ctx)
    proj_tok = app_state.current_project_ctx.set(None)
    try:
        yield ctx
    finally:
        app_state.current_user_ctx.reset(user_tok)
        app_state.current_project_ctx.reset(proj_tok)


@pytest.fixture
def wired(db_session_factory, tmp_path, monkeypatch):
    prev = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    monkeypatch.setattr("app.config.settings.DASHBOARDS_LOCAL_DIR", str(tmp_path / "dash"))
    try:
        yield
    finally:
        app_state.db_session_factory = prev


@pytest.mark.asyncio
async def test_mcp_guide_validate_deploy_list_get_delete(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        guide = await _tool(server, "get_dashboard_authoring_guide")()
        assert "fluxito.query" in guide["guide"]
        assert "manifest.json" in guide["guide"]
        assert guide["kind"] == "web"
        assert guide["recipes"]["ga4"]["action"] == "run_report"
        recipe = await _tool(server, "get_dashboard_query_recipe")(connection_type="ga4")
        assert recipe["action"] == "run_report"
        assert "property_id" in recipe["injected"]

        ok = await _tool(server, "validate_dashboard_artifact")(files=_files())
        assert ok["ok"] is True

        bad = await _tool(server, "validate_dashboard_artifact")(
            files={**_files(), ".env": "SECRET=abc12345"}
        )
        assert bad["ok"] is False
        assert bad["error_type"] == "invalid_artifact"

        deployed = await _tool(server, "deploy_dashboard")(title="GA4 last 30 days", files=_files())
        assert "error" not in deployed or not deployed.get("error")
        assert deployed["kind"] == "hosted"
        dash_id = deployed["dashboard_id"]
        slug = deployed["slug"]
        assert "/live-dashboards/" in deployed["url"]
        assert deployed["host_status"] == "ready"
        assert deployed["bindings"][0]["alias"] == "ga4"
        assert deployed["bindings"][0]["status"] == "missing"

        listed = await _tool(server, "list_dashboards")()
        assert listed["total"] == 1
        assert listed["dashboards"][0]["id"] == dash_id
        assert "deploy_dashboard" in listed["create_hint"]
        assert "bind_dashboard" in listed["create_hint"]
        assert "dashboard_deploy_batch" not in listed["create_hint"]

        got = await _tool(server, "get_dashboard")(dashboard_id=dash_id)
        assert got["dashboard"]["kind"] == "hosted"
        assert got["dashboard"]["slug"] == slug

        updated = await _tool(server, "update_dashboard")(
            dashboard_id=dash_id,
            files=_files(),
            title="GA4 refreshed",
        )
        assert updated.get("error") is not True

        conns = await _tool(server, "list_dashboard_connections")()
        assert "connections" in conns
        assert "secrets" in conns["hint"].lower() or "Never inline" in conns["hint"]

        deleted = await _tool(server, "delete_dashboard")(dashboard_id=dash_id)
        assert deleted["success"] is True

    async with db_session_factory() as db:
        remaining = (await db.execute(select(Dashboard))).scalars().all()
        assert remaining == []


@pytest.mark.asyncio
async def test_deploy_rejects_secrets(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "deploy_dashboard")(
            title="Bad",
            files={**_files(), "app.js": _app_js() + "\npassword = 'hunter2secret'\n"},
        )
    assert resp.get("error") is True
    assert resp.get("error_type") == "invalid_artifact"


@pytest.mark.asyncio
async def test_data_plane_refresh_uses_bound_alias(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    dash_id = uuid.UUID(deployed["dashboard_id"])

    async with db_session_factory() as db:
        dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_id))).scalar_one()
        dash.connection_bindings = [
            {
                "alias": "ga4",
                "type": "ga4",
                "status": "bound",
                "tool": "analytics_read",
                "resource_key": "property_id",
                "resource_value": "279951751",
            }
        ]
        await db.commit()
        await db.refresh(dash)

    seen = {}

    async def _ga4(call_args):
        seen.update(call_args)
        return {
            "dimension_headers": ["date"],
            "metric_headers": ["sessions"],
            "rows": [{"dimension_values": ["2026-01-01"], "metric_values": ["10"]}],
        }

    class _TM:
        _tools = {"analytics_read": _FakeTool(_ga4)}
        _legacy_tools = {}

    from unittest.mock import AsyncMock, patch

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    with patch("app.auth.mcp_session_manager.build_refresh_context", new=AsyncMock(return_value=_Ctx())):
        result = await run_alias_query(
            dash,
            alias="ga4",
            action="run_report",
            params={"metrics": ["sessions"], "start_date": "2026-01-01", "end_date": "2026-01-31"},
            tool_manager=_TM(),
        )
    assert result.get("error") is not True
    assert seen.get("property_id") == "279951751"
    assert seen.get("action") == "run_report"


@pytest.mark.asyncio
async def test_data_plane_unknown_alias(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    async with db_session_factory() as db:
        dash = (
            await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(deployed["dashboard_id"])))
        ).scalar_one()
    result = await run_alias_query(dash, alias="nope", action="run_report", params={}, tool_manager=object())
    assert result["error"] is True
    assert result["error_type"] == "unknown_alias"


@pytest.mark.asyncio
async def test_hosted_view_and_status_path(wired, db_session_factory):
    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.auth.uid_cookie import sign_uid
    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]

    csrf = _generate_csrf_token()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf, "uid": sign_uid(str(uid))},
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    ) as client:
        view = await client.get(f"/live-dashboards/{slug}")
        assert view.status_code == 200
        assert "Hosted Streamlit app" not in view.text
        assert "/s/" in view.text
        assert "Add card" not in view.text
        assert (
            'sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"' in view.text
        )
        assert "allow-top-navigation" not in view.text
        assert "fluxito-embed" in view.text

        status = await client.get(f"/api/hosted-dashboards/{slug}/status")
        assert status.status_code == 200
        body = status.json()
        assert body["kind"] == "hosted"
        assert body["slug"] == slug
        assert body["host_status"] == "ready"

        hub = await client.get("/live-dashboards")
        assert hub.status_code == 200
        assert "get_dashboard_authoring_guide" in hub.text
        assert "Build with Ask Fluxito" not in hub.text


def test_hosted_view_iframe_is_sandboxed():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboards" / "hosted_view.html"
    ).read_text()
    assert 'sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"' in html
    assert "allow-top-navigation" not in html


def test_nginx_isolates_dash_origin():
    conf = (Path(__file__).resolve().parents[1] / "nginx.conf").read_text()
    assert "server_name dash.fluxito.app" in conf
    assert "listen 8002" in conf
    assert "X-Fluxito-Surface dash" in conf
    assert "location /hosted/" not in conf


def test_uid_cookie_is_host_only():
    from pathlib import Path as P

    src = (P(__file__).resolve().parents[1] / "app" / "api" / "setup_routes.py").read_text()
    # The uid cookie must never set Domain=.fluxito.app
    assert 'set_cookie(\n        "uid"' in src or 'set_cookie("uid"' in src.replace(" ", "")
    assert "domain=" not in src.lower() or 'domain="' not in src.lower()


@pytest.mark.asyncio
async def test_dash_origin_lockdown_and_query(wired, db_session_factory):
    from unittest.mock import AsyncMock, patch

    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.auth.uid_cookie import sign_uid
    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]
    dash_id = uuid.UUID(deployed["dashboard_id"])

    async with db_session_factory() as db:
        dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_id))).scalar_one()
        dash.connection_bindings = [
            {
                "alias": "ga4",
                "type": "ga4",
                "status": "bound",
                "tool": "analytics_read",
                "resource_key": "property_id",
                "resource_value": "279951751",
            }
        ]
        await db.commit()

    csrf = _generate_csrf_token()
    seen: dict = {}

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    with (
        patch("app.auth.mcp_session_manager.build_refresh_context", new=AsyncMock(return_value=_Ctx())),
        patch("app.dashboards.data_plane.query_engine.run_card") as run_card,
    ):

        async def _run(tm, spec, tool_name=None, action=None, timeout=None):
            seen["dispatched_tool"] = tool_name
            seen.update(spec)
            return {"rows": [{"sessions": "3"}]}

        run_card.side_effect = _run
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"csrf_token": csrf, "uid": sign_uid(str(uid))},
            headers={"x-csrf-token": csrf},
        ) as client:
            blocked = await client.post(
                "/query",
                json={"alias": "ga4", "action": "run_report", "params": {}},
            )
            assert blocked.status_code == 404

            api_query = await client.post(
                f"/api/hosted-dashboards/{slug}/query",
                json={"alias": "ga4", "action": "run_report", "params": {}},
            )
            assert api_query.status_code == 404

            session = await client.get(f"/api/hosted-dashboards/{slug}/embed-session")
            assert session.status_code == 200
            token = session.json()["token"]

            page = await client.get(
                f"/s/{slug}/",
                headers={"X-Fluxito-Surface": "dash"},
            )
            assert page.status_code == 200
            assert b"/fluxito.js" in page.content
            assert "text/html" in page.headers.get("content-type", "")
            assert "Content-Security-Policy" in page.headers
            assert "connect-src 'self'" in page.headers["Content-Security-Policy"]

            billing = await client.get(
                "/api/health",
                headers={"X-Fluxito-Surface": "dash"},
            )
            assert billing.status_code == 404

            bad = await client.post(
                "/query",
                headers={"X-Fluxito-Surface": "dash", "Authorization": "Bearer nope"},
                json={"alias": "ga4", "action": "run_report", "params": {}},
            )
            assert bad.status_code == 403

            ok = await client.post(
                "/query",
                headers={"X-Fluxito-Surface": "dash", "Authorization": f"Bearer {token}"},
                json={
                    "alias": "ga4",
                    "action": "run_report",
                    "tool": "warehouse_query",
                    "params": {"metrics": ["sessions"], "property_id": "evil", "tool": "warehouse_query"},
                },
            )
    assert ok.status_code == 200
    assert seen.get("dispatched_tool") == "analytics_read"
    assert seen.get("property_id") == "279951751"


def test_new_mcp_tools_describe_the_contract():
    server = _build_server()
    names = set(server._tool_manager._tools)
    for name in (
        "get_dashboard_authoring_guide",
        "get_dashboard_query_recipe",
        "validate_dashboard_artifact",
        "deploy_dashboard",
        "update_dashboard",
        "list_dashboards",
        "get_dashboard",
        "delete_dashboard",
        "list_dashboard_connections",
        "bind_dashboard",
    ):
        assert name in names
        doc = (server._tool_manager._tools[name].fn.__doc__ or "").lower()
        assert len(doc) >= 20
    for retired in (
        "dashboard_deploy_batch",
        "dashboard_create",
        "dashboard_card_upsert",
        "dashboard_card_remove",
        "dashboard_card_preview",
    ):
        assert retired not in names
    guide_doc = server._tool_manager._tools["get_dashboard_authoring_guide"].fn.__doc__ or ""
    assert "html" in guide_doc.lower() or "jsx" in guide_doc.lower() or "web" in guide_doc.lower()
    deploy_doc = server._tool_manager._tools["deploy_dashboard"].fn.__doc__ or ""
    assert (
        "secret" in deploy_doc.lower() or "credential" in deploy_doc.lower() or "alias" in deploy_doc.lower()
    )
    bind_doc = server._tool_manager._tools["bind_dashboard"].fn.__doc__ or ""
    assert "tool" in bind_doc.lower() and "cannot" in bind_doc.lower()
    recipe_doc = server._tool_manager._tools["get_dashboard_query_recipe"].fn.__doc__ or ""
    assert "fluxito.query" in recipe_doc.lower() or "action" in recipe_doc.lower()


def test_inject_bound_resource_overwrites_caller_identities():
    from app.dashboards.data_plane import _inject_bound_resource

    merged = _inject_bound_resource(
        {
            "type": "ga4",
            "resource_key": "property_id",
            "resource_value": "279951751",
            "account_id": "bound-account",
            "connection_id": "bound-connection",
        },
        {
            "metrics": ["sessions"],
            "property_id": "attacker-property",
            "account_id": "attacker-account",
            "connection_id": "attacker-connection",
            "tool": "warehouse_query",
        },
    )
    assert merged["property_id"] == "279951751"
    assert merged["account_id"] == "bound-account"
    assert merged["connection_id"] == "bound-connection"
    assert "tool" not in merged
    assert merged["metrics"] == ["sessions"]


@pytest.mark.asyncio
async def test_run_alias_query_ignores_caller_tool_and_overwrites_resource():
    from unittest.mock import AsyncMock, patch

    from app.dashboards.artifact import CONNECTION_TOOL

    class _Dash:
        id = uuid.uuid4()
        connection_bindings = [
            {
                "alias": "ga4",
                "type": "ga4",
                "status": "bound",
                "tool": "analytics_write",
                "resource_key": "property_id",
                "resource_value": "279951751",
                "account_id": "bound-account",
                "connection_id": "bound-connection",
            }
        ]

    seen: dict = {}
    dispatched: list[str] = []

    async def _ga4(call_args):
        dispatched.append("analytics_read")
        seen.update(call_args)
        return {"rows": [{"sessions": "10"}]}

    async def _warehouse(call_args):
        dispatched.append("warehouse_query")
        raise AssertionError("caller tool override must not dispatch warehouse_query")

    async def _write(call_args):
        dispatched.append("analytics_write")
        raise AssertionError("hostile stored binding.tool must not dispatch analytics_write")

    class _TM:
        _tools = {
            "analytics_read": _FakeTool(_ga4),
            "warehouse_query": _FakeTool(_warehouse),
            "analytics_write": _FakeTool(_write),
        }
        _legacy_tools = {}

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    with patch("app.auth.mcp_session_manager.build_refresh_context", new=AsyncMock(return_value=_Ctx())):
        result = await run_alias_query(
            _Dash(),
            alias="ga4",
            action="run_report",
            params={
                "metrics": ["sessions"],
                "property_id": "attacker-property",
                "account_id": "attacker-account",
                "connection_id": "attacker-connection",
                "tool": "warehouse_query",
            },
            tool="warehouse_query",
            tool_manager=_TM(),
        )
    assert result.get("error") is not True
    assert dispatched == ["analytics_read"]
    assert CONNECTION_TOOL["ga4"] == "analytics_read"
    assert seen["property_id"] == "279951751"
    assert seen["account_id"] == "bound-account"
    assert seen["connection_id"] == "bound-connection"
    assert seen.get("tool") not in {"warehouse_query", "analytics_write"}
    assert seen["action"] == "run_report"


@pytest.mark.asyncio
async def test_run_alias_query_fails_closed_on_unknown_type_even_with_stored_tool():
    class _Dash:
        id = uuid.uuid4()
        connection_bindings = [
            {
                "alias": "mystery",
                "type": "not_a_real_platform",
                "status": "bound",
                "tool": "analytics_write",
            }
        ]

    async def _write(call_args):
        raise AssertionError("unknown platform must not execute stored binding.tool")

    class _TM:
        _tools = {"analytics_write": _FakeTool(_write)}
        _legacy_tools = {}

    result = await run_alias_query(
        _Dash(), alias="mystery", action="run_report", params={}, tool_manager=_TM()
    )
    assert result["error"] is True
    assert result["error_type"] == "no_tool"


@pytest.mark.asyncio
async def test_bind_dashboard_mcp_rebinds_and_rejects_tool(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
        dash_id = deployed["dashboard_id"]
        rebound = await _tool(server, "bind_dashboard")(dashboard_id=dash_id)
        assert rebound.get("error") is not True
        assert rebound["kind"] == "hosted"
        assert rebound["bindings"][0]["alias"] == "ga4"

        rejected = await _tool(server, "bind_dashboard")(
            dashboard_id=dash_id,
            bindings=[{"alias": "ga4", "type": "ga4", "tool": "warehouse_query"}],
        )
        assert rejected.get("error") is True
        assert rejected.get("error_type") == "tool_not_allowed"
