"""Hosted Streamlit dashboard contract: validate, deploy, bind, host path."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

import app.app_state as app_state
from app.auth.mcp_session_manager import UserContext
from app.dashboards.artifact import ArtifactError, validate_artifact
from app.dashboards.authoring_guide import AUTHORING_GUIDE, authoring_guide_payload
from app.dashboards.connections import bind_requirements
from app.dashboards.data_plane import run_alias_query
from app.dashboards.runtime import (
    build_child_env,
    child_env_is_clean,
    set_process_factory,
    stop_all,
    stop_dashboard,
)
from app.models.dashboard import Dashboard
from app.models.user import User
from app.tools.dashboard_tools import register_dashboard_tools

# ---------------------------------------------------------------------------
# Sample artifact
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "title": "GA4 last 30 days",
        "entrypoint": "app.py",
        "connections": [{"alias": "ga4", "type": "ga4", "required": True}],
    }
    data.update(overrides)
    return data


def _app_py() -> str:
    return (
        "import streamlit as st\n"
        "import fluxito_data as fx\n\n"
        "st.set_page_config(page_title='GA4', layout='wide')\n"
        "st.title('GA4 last 30 days')\n"
        "data = fx.query('ga4', action='run_report', params={'metrics': ['sessions']})\n"
        "if data.get('error'):\n"
        "    st.error(data.get('message'))\n"
        "else:\n"
        "    st.write(data)\n"
    )


def _files(**extra) -> dict[str, str]:
    files = {
        "manifest.json": json.dumps(_manifest()),
        "app.py": _app_py(),
    }
    files.update(extra)
    return files


class _FakeTool:
    def __init__(self, fn):
        self._fn = fn

    async def run(self, call_args, *_a, **_k):
        return await self._fn(call_args)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_happy_path():
    art = validate_artifact(_files())
    assert art.manifest.entrypoint == "app.py"
    assert art.manifest.connections[0].alias == "ga4"
    assert art.digest


def test_validate_rejects_secrets():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(
            _files(
                **{
                    "app.py": _app_py() + "\nAPI_KEY = 'sk-live-supersecrettoken'\n",
                }
            )
        )
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


def test_validate_requires_fluxito_data_when_connections_present():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(
            {
                "manifest.json": json.dumps(_manifest()),
                "app.py": "import streamlit as st\nst.title('no data helper')\n",
            }
        )
    assert any("fluxito_data" in e for e in exc.value.errors)


def test_validate_rejects_card_json_and_st_secrets():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(
            {
                "manifest.json": json.dumps(_manifest()),
                "app.py": (
                    "import streamlit as st\n"
                    "import fluxito_data as fx\n"
                    "st.secrets['token']\n"
                    "chart_type = 'line'\n"
                ),
            }
        )
    joined = " ".join(exc.value.errors).lower()
    assert "st.secrets" in joined or "secrets" in joined
    assert "card" in joined or "chart_type" in joined or "retired" in joined


def test_validate_rejects_non_streamlit_entrypoint():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({"manifest.json": json.dumps(_manifest()), "app.py": "print('hello')\n"})
    assert any("streamlit" in e.lower() for e in exc.value.errors)


def test_validate_rejects_subprocess():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact(
            {
                "manifest.json": json.dumps(_manifest()),
                "app.py": "import streamlit as st\nimport subprocess\nst.write(subprocess.getoutput('ls'))\n",
            }
        )
    assert any("subprocess" in e.lower() for e in exc.value.errors)


def test_validate_rejects_path_traversal():
    with pytest.raises(ArtifactError) as exc:
        validate_artifact({**_files(), "../evil.py": "import streamlit as st\n"})
    assert any("traversal" in e.lower() or ".." in e for e in exc.value.errors)


def test_authoring_guide_is_the_contract():
    payload = authoring_guide_payload()
    guide = payload["guide"]
    assert "streamlit" in guide.lower()
    assert "manifest.json" in guide
    assert "fluxito_data" in guide
    assert "do not put credentials" in guide.lower() or "never put secrets" in guide.lower()
    assert "validate_dashboard_artifact" in guide
    assert "deploy_dashboard" in guide
    assert "bind_dashboard" in guide
    assert "tool=None" not in guide
    assert "unregistered" in guide.lower() or "do not call" in guide.lower()
    assert AUTHORING_GUIDE
    assert "ga4" in payload["connection_types"]
    assert "recipes" in payload
    assert payload["recipes"]["ga4"]["action"] == "run_report"
    assert "metrics" in payload["recipes"]["ga4"]["send"]
    assert "property_id" in payload["recipes"]["ga4"]["injected"]
    from app.dashboards.query_recipes import assert_recipes_cover_types

    assert assert_recipes_cover_types() == []


# ---------------------------------------------------------------------------
# Child env isolation
# ---------------------------------------------------------------------------


def test_child_env_has_no_fluxito_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-leak")
    env = build_child_env(
        workdir=tmp_path,
        data_url="http://127.0.0.1:8001/api/hosted-dashboards/abc/query",
        runtime_token="tok",
        dashboard_id="d1",
        bindings=[{"alias": "ga4", "type": "ga4", "status": "bound"}],
        port=14101,
        base_path="/hosted/abc",
    )
    assert child_env_is_clean(env)
    assert "DATABASE_URL" not in env
    assert "TOKEN_ENCRYPTION_KEY" not in env
    assert env["FLUXITO_RUNTIME_TOKEN"] == "tok"
    assert "ga4" in env["FLUXITO_CONNECTION_ALIASES"]
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-certificates.crt"


def test_requirements_pin_streamlit_below_starlette_server():
    """1.57+ needs Starlette APIs FastAPI 0.115 / Starlette 0.41 do not have."""
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    assert "streamlit>=1.40,<1.57" in text


def test_start_dashboard_includes_host_log_on_crash(tmp_path):
    from app.dashboards.runtime import HOST_LOG_NAME, start_dashboard, stop_dashboard

    workdir = tmp_path / "crash"
    workdir.mkdir()
    (workdir / HOST_LOG_NAME).write_text("ImportError: cannot import name 'DEFAULT_EXCLUDED_CONTENT_TYPES'\n")

    class _Dead:
        pid = 99
        returncode = 1

        def poll(self):
            return 1

        def kill(self):
            pass

    def _factory(**_kwargs):
        return _Dead()

    set_process_factory(_factory)
    try:
        with pytest.raises(RuntimeError, match="DEFAULT_EXCLUDED_CONTENT_TYPES"):
            start_dashboard(
                dashboard_id="dead",
                slug="dead",
                workdir=workdir,
                entrypoint="app.py",
                env={},
                port=14111,
            )
    finally:
        set_process_factory(None)
        stop_dashboard("dead")


def test_attach_existing_reuses_foreign_worker_process(tmp_path, monkeypatch):
    from app.dashboards.runtime import (
        attach_existing,
        get_handle,
        stop_all,
    )

    workdir = tmp_path / "att"
    workdir.mkdir()
    (workdir / ".fluxito_host.json").write_text(
        '{"dashboard_id": "d-att", "slug": "att", "port": 14122, "pid": 424242}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.dashboards.runtime._pid_alive", lambda pid: pid == 424242)
    monkeypatch.setattr("app.dashboards.runtime._port_open", lambda port: port == 14122)
    try:
        handle = attach_existing("d-att", workdir)
        assert handle is not None
        assert handle.port == 14122
        assert handle.pid == 424242
        assert get_handle("d-att", workdir) is handle
    finally:
        stop_all()


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


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


def test_write_artifact_injects_helper(tmp_path):
    from app.dashboards.runtime import write_artifact

    art = validate_artifact(_files())
    write_artifact(
        tmp_path / "d1",
        art,
        bindings=[{"alias": "ga4", "type": "ga4", "status": "bound"}],
        data_url="http://127.0.0.1:8001/api/hosted-dashboards/x/query",
        runtime_token="tok",
        dashboard_id="d1",
        slug="x",
    )
    helper = (tmp_path / "d1" / "fluxito_data.py").read_text()
    assert "def query(" in helper
    assert (tmp_path / "d1" / "app.py").exists()
    assert "DATABASE_URL" not in helper
    assert "TOKEN_ENCRYPTION_KEY" not in helper


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


# ---------------------------------------------------------------------------
# MCP + DB lifecycle
# ---------------------------------------------------------------------------


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

    class _DummyProc:
        def __init__(self):
            self.pid = 4242
            self.returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            self.returncode = self.returncode if self.returncode is not None else 0

    def _factory(**kwargs):
        # Pretend Streamlit is already listening so start_dashboard's wait passes.
        import socket

        port = kwargs["port"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            pass
        else:
            sock.listen(1)

            def _accept():
                try:
                    conn, _ = sock.accept()
                    conn.close()
                except Exception:
                    pass
                finally:
                    sock.close()

            threading.Thread(target=_accept, daemon=True).start()
        return _DummyProc()

    set_process_factory(_factory)
    try:
        yield
    finally:
        stop_all()
        set_process_factory(None)
        app_state.db_session_factory = prev


@pytest.mark.asyncio
async def test_mcp_guide_validate_deploy_list_get_delete(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        guide = await _tool(server, "get_dashboard_authoring_guide")()
        assert "fluxito_data.query" in guide["guide"]
        assert "manifest.json" in guide["guide"]
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
        assert deployed["bindings"][0]["alias"] == "ga4"
        # No matching connection in the empty project → missing bind, not a crash.
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
        assert updated["title"] == "GA4 last 30 days" or updated["title"]

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
            files={**_files(), "app.py": _app_py() + "\npassword = 'hunter2secret'\n"},
        )
    assert resp.get("error") is True
    assert resp.get("error_type") == "invalid_artifact"


# ---------------------------------------------------------------------------
# Data plane bind/refresh
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Host / embed HTTP path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hosted_proxy_and_status_path(wired, db_session_factory, tmp_path, monkeypatch):
    """The reporting host path proxies to the isolated process (dummy HTTP here)."""
    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.auth.uid_cookie import sign_uid
    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()

    # Real tiny HTTP server stands in for Streamlit so the proxy is exercised.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"hosted-ok {self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def _factory(**kwargs):
        class _P:
            pid = 99
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

        return _P()

    set_process_factory(_factory)

    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]
    dash_id = uuid.UUID(deployed["dashboard_id"])

    async with db_session_factory() as db:
        dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_id))).scalar_one()
        dash.host_status = "running"
        dash.host_port = port
        dash.is_public = True
        await db.commit()

    import subprocess

    from app.dashboards.runtime import HostedProcess, _processes

    class _Alive(subprocess.Popen):
        def __init__(self):
            pass

        def poll(self):
            return None

    # Point the in-memory supervisor at the dummy server port.
    stop_dashboard(str(dash_id))
    _processes[str(dash_id)] = HostedProcess(
        dashboard_id=str(dash_id),
        slug=slug,
        port=port,
        pid=1,
        workdir=tmp_path,
        proc=_Alive(),  # type: ignore[arg-type]
    )

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
        assert "Hosted Streamlit app" in view.text
        assert f"/hosted/{slug}/" in view.text
        assert "Add card" not in view.text
        assert (
            'sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"' in view.text
        )
        assert "allow-top-navigation" not in view.text

        proxied = await client.get(f"/hosted/{slug}/")
        assert proxied.status_code == 200
        assert "hosted-ok" in proxied.text

        status = await client.get(f"/api/hosted-dashboards/{slug}/status")
        assert status.status_code == 200
        body = status.json()
        assert body["kind"] == "hosted"
        assert body["slug"] == slug

        hub = await client.get("/live-dashboards")
        assert hub.status_code == 200
        assert "get_dashboard_authoring_guide" in hub.text
        assert "Build with Ask Fluxito" not in hub.text

    httpd.shutdown()
    stop_dashboard(str(dash_id))


def test_forward_request_headers_strips_viewer_credentials():
    from starlette.requests import Request

    from app.api.dashboard_routes import _forward_request_headers

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/hosted/foo/",
        "raw_path": b"/hosted/foo/",
        "query_string": b"",
        "headers": [
            (b"host", b"fluxito.example"),
            (b"cookie", b"uid=secret-session"),
            (b"authorization", b"Bearer viewer-jwt"),
            (b"proxy-authorization", b"Basic abc"),
            (b"set-cookie", b"uid=should-not-forward"),
            (b"cookie2", b"legacy"),
            (b"x-csrf-token", b"csrf-secret"),
            (b"x-xsrf-token", b"xsrf-secret"),
            (b"connection", b"keep-alive"),
            (b"keep-alive", b"timeout=5"),
            (b"transfer-encoding", b"chunked"),
            (b"accept", b"text/html"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
    }
    forwarded = {k.lower(): v for k, v in _forward_request_headers(Request(scope)).items()}
    for name in (
        "cookie",
        "authorization",
        "proxy-authorization",
        "set-cookie",
        "cookie2",
        "x-csrf-token",
        "x-xsrf-token",
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
    ):
        assert name not in forwarded
    assert forwarded["accept"] == "text/html"
    assert forwarded["content-type"] == "application/json"


def test_hosted_view_iframe_is_sandboxed():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboards" / "hosted_view.html"
    ).read_text()
    assert 'sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"' in html
    assert "allow-top-navigation" not in html


def test_nginx_hosted_location_upgrades_websocket():
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[1] / "nginx.conf").read_text()
    assert "location /hosted/" in conf
    assert "proxy_set_header   Upgrade           $http_upgrade;" in conf
    assert "map $http_upgrade $connection_upgrade" in conf
    hosted = conf.split("location /hosted/")[1].split("location ")[0]
    assert "Connection        $connection_upgrade" in hosted
    assert 'Connection "";' not in hosted


def test_upstream_ws_connect_kwargs_compatible():
    pytest.importorskip("websockets")
    from app.api.dashboard_routes import _upstream_ws_connect_kwargs

    kwargs = _upstream_ws_connect_kwargs(14100)
    assert "additional_headers" in kwargs or "extra_headers" in kwargs
    headers = kwargs.get("additional_headers") or kwargs.get("extra_headers")
    assert headers["Host"] == "127.0.0.1:14100"


@pytest.mark.asyncio
async def test_hosted_proxy_strips_viewer_credentials(wired, db_session_factory, tmp_path):
    """Cookie / Authorization / Proxy-Authorization must never reach Streamlit."""
    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.auth.uid_cookie import sign_uid
    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured.clear()
            captured.update({k.lower(): v for k, v in self.headers.items()})
            body = b"hosted-ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def _factory(**kwargs):
        class _P:
            pid = 99
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

        return _P()

    set_process_factory(_factory)

    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]
    dash_id = uuid.UUID(deployed["dashboard_id"])

    async with db_session_factory() as db:
        dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_id))).scalar_one()
        dash.host_status = "running"
        dash.host_port = port
        dash.is_public = True
        await db.commit()

    import subprocess

    from app.dashboards.runtime import HostedProcess, _processes

    class _Alive(subprocess.Popen):
        def __init__(self):
            pass

        def poll(self):
            return None

    stop_dashboard(str(dash_id))
    _processes[str(dash_id)] = HostedProcess(
        dashboard_id=str(dash_id),
        slug=slug,
        port=port,
        pid=1,
        workdir=tmp_path,
        proc=_Alive(),  # type: ignore[arg-type]
    )

    csrf = _generate_csrf_token()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"csrf_token": csrf, "uid": sign_uid(str(uid))},
            headers={
                "x-csrf-token": csrf,
                "Authorization": "Bearer viewer-jwt",
                "Proxy-Authorization": "Basic abc",
            },
            follow_redirects=False,
        ) as client:
            proxied = await client.get(f"/hosted/{slug}/")
            assert proxied.status_code == 200
            assert "hosted-ok" in proxied.text
    finally:
        httpd.shutdown()
        stop_dashboard(str(dash_id))

    assert captured
    assert "cookie" not in captured
    assert "authorization" not in captured
    assert "proxy-authorization" not in captured
    assert "x-csrf-token" not in captured
    assert "x-xsrf-token" not in captured


@pytest.mark.asyncio
async def test_hosted_query_endpoint_rejects_bad_token(wired, db_session_factory):
    import httpx
    from httpx import ASGITransport

    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            f"/api/hosted-dashboards/{slug}/query",
            json={"alias": "ga4", "action": "run_report", "params": {}},
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 403


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
    assert "streamlit" in guide_doc.lower()
    deploy_doc = server._tool_manager._tools["deploy_dashboard"].fn.__doc__ or ""
    assert (
        "secret" in deploy_doc.lower() or "credential" in deploy_doc.lower() or "alias" in deploy_doc.lower()
    )
    bind_doc = server._tool_manager._tools["bind_dashboard"].fn.__doc__ or ""
    assert "tool" in bind_doc.lower() and "cannot" in bind_doc.lower()
    recipe_doc = server._tool_manager._tools["get_dashboard_query_recipe"].fn.__doc__ or ""
    assert "fluxito_data" in recipe_doc.lower() or "action" in recipe_doc.lower()


def test_inject_bound_resource_overwrites_caller_identities():
    """Bound property_id / account_id / connection_id always beat the caller."""
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
    """Hostile stored binding.tool + attacker resource ids must not retarget dispatch."""
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
    """Unknown binding.type must not fall back to a tampered binding.tool."""

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


def test_fluxito_data_query_does_not_accept_or_post_tool():
    import inspect

    from app.dashboards import fluxito_data

    sig = inspect.signature(fluxito_data.query)
    assert "tool" not in sig.parameters
    source = inspect.getsource(fluxito_data.query)
    assert '"tool"' not in source
    assert "tool=" not in source


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


@pytest.mark.asyncio
async def test_hosted_query_endpoint_ignores_caller_tool(wired, db_session_factory):
    from unittest.mock import AsyncMock, patch

    import httpx
    from httpx import ASGITransport

    from app.main import app

    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        deployed = await _tool(server, "deploy_dashboard")(title="GA4", files=_files())
    slug = deployed["slug"]
    dash_id = uuid.UUID(deployed["dashboard_id"])

    async with db_session_factory() as db:
        dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_id))).scalar_one()
        dash.runtime_token = "tok"
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
        ) as client:
            resp = await client.post(
                f"/api/hosted-dashboards/{slug}/query",
                json={
                    "alias": "ga4",
                    "action": "run_report",
                    "tool": "warehouse_query",
                    "params": {"metrics": ["sessions"], "property_id": "evil", "tool": "warehouse_query"},
                },
                headers={"Authorization": "Bearer tok"},
            )
    assert resp.status_code == 200
    assert seen.get("dispatched_tool") == "analytics_read"
    assert seen.get("property_id") == "279951751"
