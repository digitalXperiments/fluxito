"""
Application Entry Point

Performance optimizations:
  - Single MCP server instance (tools registered once at startup)
  - Background task for batched DB writes (last_used_at)
  - Proper Redis connection pooling
  - Graceful shutdown with resource cleanup
"""

import asyncio
import hashlib as _hl
import hmac as _hm
import logging
import traceback
import uuid as _uuid
from contextlib import asynccontextmanager
from pathlib import Path as _Path

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select as _sel

import app.app_state as app_state
from app.auth.mcp_session_manager import build_project_context, require_valid_mcp_token
from app.auth.oauth_app_credentials import OAuthAppNotConfigured as _OAuthAppNotConfigured
from app.config import settings
from app.db.database import AsyncSessionLocal, engine
from app.logging_config import configure_logging
from app.models.project import Project, ProjectMember

# Configure structured logging before anything else logs
configure_logging()

logger = logging.getLogger(__name__)


def _mask_bearer(auth_header: str) -> str:
    """Return a safe-to-log representation of an Authorization header.

    Shows only the first 4 and last 4 chars of the token so that raw
    values never hit logs / Sentry / CloudWatch. Short tokens collapse to
    a blanket '***' placeholder.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return "Bearer ***"
    token = auth_header[7:].strip()
    if len(token) <= 8:
        return "Bearer ***"
    return f"Bearer {token[:4]}...{token[-4:]}"


# ---------------------------------------------------------------------------
# Single MCP server — created once, tools registered once
# ---------------------------------------------------------------------------
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.tools.registry import register_all_tools

# Disable MCP's built-in DNS rebinding protection — we live behind a
# reverse proxy (Nginx) that already validates Host, and our own auth
# middleware requires a valid Bearer token before the request ever
# reaches the MCP handler. Leaving it on would force a per-env
# allowed_hosts list and reject internal health-check Host values.
_mcp_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)

mcp_server = FastMCP(
    name=settings.MCP_SERVER_NAME,
    transport_security=_mcp_transport_security,
    # Cross-client robustness. The default streamable-HTTP transport (a) issues
    # a per-session ``Mcp-Session-Id`` the client must echo on every request and
    # (b) replies with an SSE ``text/event-stream``. Claude tolerates both, but
    # stricter clients (e.g. Grok) that mishandle the session id or wait on the
    # stream simply time out. This server already authenticates via a Bearer
    # token and resolves the active project per request from Redis (see
    # ``_apply_project_context`` below) — it keeps no per-session state — so a
    # stateless, single-JSON-response transport is both safe and the documented
    # intent, and it works uniformly across every MCP client.
    stateless_http=True,
    json_response=True,
)
register_all_tools(mcp_server)


# ---------------------------------------------------------------------------
# Background task: flush batched last_used_at updates every 60s
# ---------------------------------------------------------------------------

_bg_tasks: list[asyncio.Task] = []


async def _periodic_flush_last_used():
    """Background task that flushes last_used_at updates from Redis to DB."""
    from app.auth.mcp_session_manager import flush_last_used_batch

    while True:
        try:
            await asyncio.sleep(30)
            await flush_last_used_batch()
        except asyncio.CancelledError:
            # Final flush on shutdown
            try:
                await flush_last_used_batch()
            except Exception:
                pass
            break
        except Exception as e:
            logger.warning(f"Background flush error: {e}")
            await asyncio.sleep(10)


async def _init_sentry_from_runtime_settings() -> None:
    """Initialize Sentry from DB-backed runtime settings with env fallback."""
    from app.settings_service import get_runtime_setting

    try:
        async with AsyncSessionLocal() as db:
            sentry_dsn = await get_runtime_setting(db, "sentry_dsn")
            traces_sample_rate = await get_runtime_setting(db, "sentry_traces_sample_rate")
    except Exception as exc:
        logger.warning("Could not load Sentry runtime settings; using env/default fallback: %s", exc)
        sentry_dsn = settings.SENTRY_DSN
        traces_sample_rate = settings.SENTRY_TRACES_SAMPLE_RATE

    if not sentry_dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=float(traces_sample_rate or 0.1),
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            environment=settings.APP_ENV,
            release=f"fluxito@{settings.MCP_SERVER_VERSION}",
            send_default_pii=False,
        )
        logger.info("Sentry initialized (DSN=...%s)", sentry_dsn[-12:])
    except ImportError:
        logger.warning("Sentry DSN is set but sentry-sdk is not installed — skipping")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    redis = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
        max_connections=50,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=True,
    )

    from app.auth.google_token_manager import GoogleTokenManager
    from app.connectors.adobe_analytics import AdobeAnalyticsConnector
    from app.connectors.adobe_launch import AdobeLaunchConnector
    from app.connectors.adobe_marketo import AdobeMarketoConnector
    from app.connectors.amplitude import AmplitudeConnector
    from app.connectors.apple_ads import AppleAdsConnector
    from app.connectors.bigquery import BigQueryConnector
    from app.connectors.bing_webmaster import BingWebmasterConnector
    from app.connectors.ga4 import GA4Connector
    from app.connectors.google_ads import GoogleAdsConnector
    from app.connectors.gtm import GTMConnector
    from app.connectors.linkedin_ads import LinkedInAdsConnector
    from app.connectors.meta_ads import MetaAdsConnector
    from app.connectors.pinterest_ads import PinterestAdsConnector
    from app.connectors.reddit_ads import RedditAdsConnector
    from app.connectors.redshift import RedshiftConnector
    from app.connectors.search_console import SearchConsoleConnector
    from app.connectors.snap_ads import SnapAdsConnector
    from app.connectors.snowflake import SnowflakeConnector
    from app.connectors.tiktok_ads import TikTokAdsConnector

    token_mgr = GoogleTokenManager(redis, AsyncSessionLocal)

    # Populate module-level singletons
    app_state.redis_client = redis
    app_state.db_session_factory = AsyncSessionLocal
    app_state.token_manager = token_mgr
    app_state.ga4_connector = GA4Connector(token_mgr)
    app_state.gtm_connector = GTMConnector(token_mgr)
    app_state.ads_connector = GoogleAdsConnector(token_mgr)
    app_state.search_console_connector = SearchConsoleConnector(token_mgr)
    app_state.bq_connector = BigQueryConnector()
    app_state.meta_connector = MetaAdsConnector()
    app_state.tiktok_connector = TikTokAdsConnector()
    app_state.snap_connector = SnapAdsConnector()
    app_state.linkedin_connector = LinkedInAdsConnector()
    app_state.pinterest_connector = PinterestAdsConnector()
    app_state.reddit_connector = RedditAdsConnector()
    app_state.bing_connector = BingWebmasterConnector()
    app_state.apple_connector = AppleAdsConnector()
    app_state.amplitude_connector = AmplitudeConnector()
    app_state.adobe_analytics_connector = AdobeAnalyticsConnector()
    app_state.adobe_launch_connector = AdobeLaunchConnector()
    app_state.adobe_marketo_connector = AdobeMarketoConnector()

    await _init_sentry_from_runtime_settings()
    app_state.redshift_connector = RedshiftConnector()
    app_state.snowflake_connector = SnowflakeConnector()

    # Seed system templates (idempotent — skips existing)
    try:
        from app.db.seed_templates import seed_templates

        await seed_templates(AsyncSessionLocal)
    except Exception as e:
        logger.warning(f"Template seeding skipped: {e}")

    # Seed system automations (idempotent — upserts curated automations)
    try:
        from app.db.seed_automations import seed_automations

        await seed_automations(AsyncSessionLocal)
    except Exception as e:
        logger.warning(f"Automation seeding skipped: {e}")

    # Start background tasks
    _bg_tasks.append(asyncio.create_task(_periodic_flush_last_used()))

    # Start the scheduled-reports worker (APScheduler w/ Redis jobstore).
    # Kept after app_state wiring so the runner can see db_session_factory.
    try:
        from app.scheduling.service import start_scheduler

        await start_scheduler(settings.REDIS_URL)
    except Exception as exc:
        # A scheduler startup failure must not prevent the API from
        # serving traffic — log loudly and keep going.
        logger.exception("Scheduled-reports worker failed to start: %s", exc)

    # Start MCP session manager — required for Streamable HTTP transport.
    # Runs for the lifetime of the FastAPI app.
    mcp_session_cm = mcp_server.session_manager.run()
    await mcp_session_cm.__aenter__()

    # Warm the branding + announcement caches so the first request renders correctly.
    try:
        from app.branding import refresh_announcement, refresh_brand

        await refresh_brand()
        await refresh_announcement()
    except Exception:
        logger.warning("Initial brand/announcement refresh failed; using defaults", exc_info=True)

    logger.info("Application started (APP_ENV=%s)", settings.APP_ENV)

    yield

    # ── Graceful shutdown ──
    logger.info("Shutting down — cancelling background tasks...")

    # Stop MCP session manager
    try:
        await mcp_session_cm.__aexit__(None, None, None)
    except Exception as exc:
        logger.warning("MCP session manager shutdown error (ignored): %s", exc)

    # Stop the scheduler first so no new jobs fire during teardown.
    try:
        from app.scheduling.service import stop_scheduler

        await stop_scheduler()
    except Exception as exc:
        logger.warning("Scheduler shutdown error (ignored): %s", exc)

    for task in _bg_tasks:
        task.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    _bg_tasks.clear()

    # Close shared HTTP client in token manager
    await token_mgr.close()

    # Close warehouse connection pools (Redshift/Snowflake)
    try:
        from app.connectors._conn_pool import redshift_pool, snowflake_pool

        redshift_pool.close_all()
        snowflake_pool.close_all()
    except Exception as e:
        logger.warning(f"Warehouse pool shutdown error: {e}")

    # Close Redis
    await redis.aclose()

    # Dispose DB engine (close pool)
    await engine.dispose()

    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fluxito Platform",
    version=settings.MCP_SERVER_VERSION,
    lifespan=lifespan,
    docs_url="/api-docs" if settings.APP_ENV == "development" else None,
    redoc_url=None,
)

# ---------------------------------------------------------------------------
# CORS — allow Claude.ai, ChatGPT, and other configured origins
# ---------------------------------------------------------------------------
_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
if settings.APP_ENV == "development":
    _cors_origins += ["http://localhost:3000", "http://localhost:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

# ---------------------------------------------------------------------------
# Static files (css, js, images) for the unified UI
# ---------------------------------------------------------------------------
_STATIC_DIR = _Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.exception_handler(_OAuthAppNotConfigured)
async def oauth_not_configured_handler(request: Request, exc: _OAuthAppNotConfigured):
    from fastapi.responses import HTMLResponse

    platform = str(exc).split("'")[1] if "'" in str(exc) else "this platform"
    return HTMLResponse(
        status_code=403,
        content=f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Not configured — Fluxito</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/css/app.css"/>
<style>
.err-shell {{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:40px 16px; }}
.err-card {{ max-width:480px; text-align:center; }}
.err-card h1 {{ font-size:24px; font-weight:600; margin:0 0 12px; color:var(--ink); letter-spacing:-0.02em; }}
.err-card p {{ font-size:14px; color:var(--ink-soft); line-height:1.6; margin:0 0 24px; }}
.err-actions {{ display:flex; gap:10px; justify-content:center; }}
</style>
</head>
<body>
<div class="err-shell">
  <div class="err-card">
    <h1>{platform.replace("_"," ").title()} not configured</h1>
    <p>OAuth credentials for <strong>{platform}</strong> haven't been set up yet.
       An admin can add them at Settings &rarr; Integrations.</p>
    <div class="err-actions">
      <a href="/settings/integrations" class="btn primary">Go to Integrations</a>
      <a href="/connect" class="btn">Back to Connections</a>
    </div>
  </div>
</div>
</body>
</html>""",
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    is_dev = settings.APP_ENV == "development"
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "error_type": "internal_error",
            "message": str(exc) if is_dev else "An internal error occurred",
            "traceback": tb if is_dev else None,
        },
    )


# ---------------------------------------------------------------------------
# HTTP Routers
# ---------------------------------------------------------------------------

from app.api.access_request_routes import router as access_request_router
from app.api.admin_routes import router as admin_router
from app.api.audit_routes import router as audit_router
from app.api.auditing_routes import router as auditing_platform_router
from app.api.auth_routes import router as auth_router
from app.api.automation_routes import router as automation_router
from app.api.connector_metadata_routes import router as connector_metadata_router
from app.api.dashboard_query_routes import router as dashboard_query_router
from app.api.dashboard_routes import router as dashboard_router
from app.api.google_oauth_routes import router as google_router
from app.api.integrations_routes import router as integrations_router
from app.api.knowledge_routes import router as knowledge_router
from app.api.notification_routes import router as notification_router
from app.api.project_routes import router as project_router
from app.api.scheduled_report_routes import router as scheduled_report_router
from app.api.setup_routes import router as setup_router
from app.api.template_routes import router as template_router
from app.api.tracking_plan_routes import router as tracking_plan_router
from app.api.update_routes import router as update_router
from app.auth.mcp_oauth_server import router as oauth_router

app.include_router(setup_router)
app.include_router(oauth_router)
app.include_router(google_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(dashboard_query_router)
app.include_router(notification_router)
app.include_router(template_router)
app.include_router(automation_router)
app.include_router(integrations_router)
app.include_router(knowledge_router)
app.include_router(connector_metadata_router)
app.include_router(audit_router)
app.include_router(auditing_platform_router)
app.include_router(project_router)
app.include_router(tracking_plan_router)
app.include_router(scheduled_report_router)
app.include_router(admin_router)
app.include_router(access_request_router)
app.include_router(update_router)

from app.api import (
    apple_oauth_routes,
    bing_oauth_routes,
    linkedin_oauth_routes,
    pinterest_oauth_routes,
    reddit_oauth_routes,
    x_oauth_routes,
)

app.include_router(linkedin_oauth_routes.router)
app.include_router(pinterest_oauth_routes.router)
app.include_router(reddit_oauth_routes.router)
app.include_router(x_oauth_routes.router)
app.include_router(bing_oauth_routes.router)
app.include_router(apple_oauth_routes.router)


# ---------------------------------------------------------------------------
# MCP via Streamable HTTP  —  mcp 1.11+ compatible
#
# Each tool call is a standalone POST to /mcp. No long-lived connection,
# no session affinity required. ContextVars are set per-request by the
# /mcp route handler below.
#
# Calling streamable_http_app() is required to lazily create the session
# manager — we don't use the returned Starlette app directly (we mount
# only the ASGI handler into a FastAPI route so our auth middleware +
# ContextVar wiring runs first), but this call is still needed.
# ---------------------------------------------------------------------------

mcp_server.streamable_http_app()  # lazily initializes mcp_server.session_manager


# ---------------------------------------------------------------------------
# First-run setup gate
# ---------------------------------------------------------------------------

# Module-level cache: None = not yet checked; True = users exist (gate off).
# Never flips back to False — once users exist the gate stays open forever.
_setup_complete: bool | None = None

# Paths that bypass the first-run gate unconditionally.
# /api/ is fully excluded so programmatic clients and the MCP OAuth flow
# are never blocked by an empty users table — they rely on their own auth.
_SETUP_BYPASS_PREFIXES = (
    "/setup",
    "/signin",
    "/request-access",
    "/auth/",
    "/api/",
    "/mcp",
    "/oauth/",
    "/static/",
    "/.well-known/",
    "/healthz",
    "/favicon",
)


async def _first_run_gate(path: str):
    """Return a Starlette Response ASGI app if the request should be intercepted.

    Returns None when the request should continue normally.
    Redirect HTML paths to /setup; return 503 JSON for /api/* paths.
    """
    global _setup_complete

    # Fast-path: once users exist, never block again.
    if _setup_complete is True:
        return None

    # The marketing landing page at "/" is public — anonymous visitors should
    # see it regardless of whether any users exist yet (the landing route
    # itself redirects logged-in users onward). Exact match only so we don't
    # accidentally bypass every path.
    if path == "/":
        return None

    # Never block setup-related or static paths.
    for prefix in _SETUP_BYPASS_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return None

    # Check DB — wrapped so a DB error never blocks the request.
    try:
        if app_state.db_session_factory is not None:
            from sqlalchemy import exists as _exists
            from sqlalchemy import select as _select

            from app.models.user import User as _User

            async with app_state.db_session_factory() as db:
                result = await db.execute(_select(_exists().where(_User.id.is_not(None))))
                if result.scalar():
                    _setup_complete = True
                    return None
        else:
            # DB not yet wired (very early in startup) — let the request through.
            return None
    except Exception as exc:
        logger.warning("First-run gate DB check failed (allowing request): %s", exc)
        return None

    # No users exist — redirect to sign-in (which shows first-run create-account UX).
    from starlette.responses import RedirectResponse as _RedirectResponse

    return _RedirectResponse("/signin", status_code=302)


# ---------------------------------------------------------------------------
# Request-scoped middleware — pure ASGI
#
# We deliberately avoid Starlette's ``@app.middleware("http")`` pattern
# (a.k.a. ``BaseHTTPMiddleware``) for anything that runs on the /mcp
# path. BaseHTTPMiddleware buffers the whole response body and
# assertion-fails on non-body ASGI messages — which is exactly what
# happens when the Streamable HTTP transport streams chunked JSON-RPC
# replies (observed in tools like ``run_script`` that take long enough
# to produce multiple chunks).
#
# Pure ASGI middleware passes scope/receive/send through untouched, so
# streaming works. Trade-off: we can't inspect the outgoing response,
# only the incoming request — but we never needed to anyway.
# ---------------------------------------------------------------------------


class _FluxitoRequestMiddleware:
    """Single pass-through middleware handling auth, CSRF, nav context.

    Runs all concerns in the correct order on every HTTP request:
      0. First-run setup gate  (redirects to /setup when users table is empty)
      1. MCP Bearer auth       (sets request.state.user_context for /mcp)
      2. CSRF double-submit    (validates header+cookie on non-safe verbs,
                                exempt paths skip; /mcp is exempt)
      3. Nav-project context   (restores active project from cookies for
                                HTML routes — skipped for api/mcp/static)

    CSRF failures synthesize a 403 response inline (no call to the inner
    app). Everything else falls through to the downstream ASGI app.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        scope.setdefault("state", {})
        state = scope["state"]

        # ── 0. First-run setup gate ───────────────────────────────────────
        # When the users table is empty, redirect all page requests to /setup.
        # The "users exist" result is cached in _setup_complete so DB is only
        # queried once (or until the cache is cleared on error).
        redirect = await _first_run_gate(path)
        if redirect is not None:
            return await redirect(scope, receive, send)

        # ── 1. MCP Bearer auth ────────────────────────────────────────────
        if path.startswith("/mcp"):
            state["user_context"] = None
            state["mcp_client_name"] = None

            from starlette.requests import Request as _StarletteRequest

            request = _StarletteRequest(scope, receive)
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    user_ctx = await require_valid_mcp_token(request)
                    state["user_context"] = user_ctx
                except Exception as exc:
                    logger.warning(
                        "MCP token validation failed for %s: %s",
                        _mask_bearer(auth_header),
                        exc,
                    )

        # ── 1b. Maintenance mode ──────────────────────────────────────────
        # When enabled, only super-admins may use the app; everyone else gets a
        # maintenance page (503). Sign-in/auth/static stay open so an admin can
        # still get in and turn it back off.
        maint = await _maintenance_gate(scope, path, state)
        if maint is not None:
            return await maint(scope, receive, send)

        # ── 2. CSRF — double-submit validation + cookie seeding ───────────
        # The pure-ASGI path only validates incoming; we wrap `send` so that
        # any outgoing response (including the 403 CSRF rejection itself)
        # sets the csrf_token cookie when the request didn't have one.
        # This replaces the BaseHTTPMiddleware-flavored `_ensure_csrf_cookie`
        # that was dropped during the ASGI-migration.
        from app.auth.csrf import _CSRF_COOKIE_NAME, _generate_csrf_token, csrf_precheck_asgi

        send_wrapped = send
        if not path.startswith(("/mcp", "/static/", "/.well-known/")):
            from starlette.requests import Request as _StarletteRequest

            req_for_cookies = _StarletteRequest(scope)
            if _CSRF_COOKIE_NAME not in req_for_cookies.cookies:
                new_token = _generate_csrf_token()
                cookie_parts = [
                    f"{_CSRF_COOKIE_NAME}={new_token}",
                    "Path=/",
                    f"Max-Age={86400}",
                    "SameSite=Lax",
                ]
                if settings.APP_ENV == "production":
                    cookie_parts.append("Secure")
                cookie_header = (b"set-cookie", "; ".join(cookie_parts).encode())

                async def send_with_cookie(message, _orig=send, _ck=cookie_header):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append(_ck)
                        message = {**message, "headers": headers}
                    await _orig(message)

                send_wrapped = send_with_cookie

        csrf_response = csrf_precheck_asgi(scope)
        if csrf_response is not None:
            return await csrf_response(scope, receive, send_wrapped)

        # ── 3. Nav-project context (cookie-auth'd web UI) ─────────────────
        if not path.startswith(("/mcp", "/api/", "/static", "/auth/", "/oauth/")):
            await _attach_nav_project_context(scope)

        await self.app(scope, receive, send_wrapped)


_MAINTENANCE_ALLOW_PREFIXES = (
    "/static",
    "/.well-known",
    "/favicon",
    "/signin",
    "/auth/",
    "/setup",
    "/healthz",
    "/oauth/",
)


async def _maintenance_gate(scope, path: str, state: dict):
    """Return an ASGI response app to short-circuit when maintenance mode is on
    and the requester is not a super-admin; otherwise None (allow through)."""
    from app.settings_service import maintenance_mode_enabled

    try:
        if not await maintenance_mode_enabled():
            return None
    except Exception:
        return None  # fail open — never lock everyone out on a settings blip

    if any(path.startswith(p) for p in _MAINTENANCE_ALLOW_PREFIXES):
        return None

    # Resolve the requester so super-admins can keep working through maintenance.
    uid = None
    if path.startswith("/mcp"):
        uctx = state.get("user_context")
        uid = str(getattr(uctx, "user_id", "") or "") if uctx else None
    else:
        from starlette.requests import Request as _StarletteRequest

        from app.auth.uid_cookie import verify_uid

        uid = verify_uid(_StarletteRequest(scope).cookies.get("uid"))

    if uid:
        from app.auth.superadmin_cache import is_superadmin_cached

        if await is_superadmin_cached(uid):
            return None

    if path.startswith(("/api/", "/mcp")):
        return JSONResponse(
            {
                "error": True,
                "error_type": "maintenance",
                "message": "Fluxito is undergoing maintenance. Please try again shortly.",
            },
            status_code=503,
        )

    from fastapi.responses import HTMLResponse

    from app.branding import brand as _brand

    name = _brand()["name"]
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{name} — Maintenance</title>"
        "<style>body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:#f7f9fc;color:#101114;display:flex;min-height:100vh;align-items:center;justify-content:center}"
        ".m{max-width:440px;padding:40px;text-align:center}.m h1{font-size:28px;margin:0 0 12px}"
        ".m p{color:#566070;line-height:1.55;margin:0 0 8px}.dot{display:inline-block;width:7px;height:7px;"
        "border-radius:99px;background:#2557f6;margin-left:2px;vertical-align:middle}</style></head>"
        f"<body><div class='m'><h1>{name}<span class='dot'></span></h1>"
        "<p>We're performing scheduled maintenance and will be back shortly.</p>"
        "<p>Thanks for your patience.</p></div></body></html>"
    )
    return HTMLResponse(html, status_code=503)


async def _attach_nav_project_context(scope) -> None:
    """Populate scope['state'] with nav_projects + active_project_* keys.

    Extracted so the ASGI middleware stays shallow. Silent on any
    failure — this is strictly a UI convenience, never load-bearing.
    """
    from starlette.requests import Request as _StarletteRequest

    request = _StarletteRequest(scope)
    uid_cookie = request.cookies.get("uid")
    active_pid_cookie = request.cookies.get("active_project_id")
    if not uid_cookie or scope["state"].get("active_project_name"):
        return

    try:
        uid_str, sig = uid_cookie.rsplit(".", 1)
        expected = _hm.new(settings.APP_SECRET_KEY.encode(), uid_str.encode(), _hl.sha256).hexdigest()
        if not _hm.compare_digest(sig, expected):
            return

        user_id = _uuid.UUID(uid_str)

        async with app_state.db_session_factory() as db:
            result = await db.execute(
                _sel(Project.id, Project.name, Project.slug, ProjectMember.role)
                .join(ProjectMember, ProjectMember.project_id == Project.id)
                .where(
                    ProjectMember.user_id == user_id,
                    ProjectMember.is_active == True,
                    Project.is_active == True,
                )
                .order_by(Project.created_at.asc())
            )
            projects = [{"id": str(r.id), "name": r.name, "slug": r.slug, "role": r.role} for r in result]
            scope["state"]["nav_projects"] = projects

            if active_pid_cookie:
                for p in projects:
                    if p["id"] == active_pid_cookie:
                        scope["state"]["active_project_name"] = p["name"]
                        scope["state"]["active_project_id"] = p["id"]
                        scope["state"]["active_project_role"] = p["role"]
                        break
            if not scope["state"].get("active_project_name") and projects:
                scope["state"]["active_project_name"] = projects[0]["name"]
                scope["state"]["active_project_id"] = projects[0]["id"]
                scope["state"]["active_project_role"] = projects[0]["role"]
    except Exception:
        pass


app.add_middleware(_FluxitoRequestMiddleware)


async def _apply_project_context(user_ctx) -> None:
    """Resolve and set current_project_ctx for an authenticated MCP request.

    Per-request (stateless HTTP) — restores the last-selected project from
    Redis, or auto-selects the sole project if the user only has one.
    Silently no-ops on errors; tools surface 'no_active_project' themselves.
    """
    try:
        restored = False
        if app_state.redis_client:
            try:
                cached_pid = await app_state.redis_client.get(f"mcp:active_project:{user_ctx.user_id}")
                if cached_pid:
                    pid_str = cached_pid.decode() if isinstance(cached_pid, bytes) else str(cached_pid)
                    if hasattr(user_ctx, "projects"):
                        for p in user_ctx.projects:
                            if p.project_id == pid_str:
                                pctx = await build_project_context(pid_str, user_ctx.user_id)
                                app_state.current_project_ctx.set(pctx)
                                _sync_user_ctx_flags(user_ctx, pctx)
                                restored = True
                                break
            except Exception as e:
                logger.debug(f"Redis project restore failed: {e}")

        if not restored and hasattr(user_ctx, "projects") and len(user_ctx.projects) == 1:
            proj = user_ctx.projects[0]
            pctx = await build_project_context(proj.project_id, user_ctx.user_id)
            app_state.current_project_ctx.set(pctx)
            _sync_user_ctx_flags(user_ctx, pctx)
    except Exception as e:
        logger.warning(f"Auto-select project failed: {e}")


def _sync_user_ctx_flags(user_ctx, pctx) -> None:
    """Mirror project-level connection flags onto the user context shim."""
    for attr in (
        "has_ga4",
        "has_gtm",
        "has_ads",
        "has_gsc",
        "has_bq",
        "has_meta",
        "has_tiktok",
        "has_snap",
        "has_linkedin",
        "has_pinterest",
        "has_x",
        "has_reddit",
        "has_bing",
        "has_apple",
        "has_amplitude",
        "has_adobe_analytics",
        "has_adobe_launch",
        "has_adobe_marketo",
        "has_redshift",
        "has_snowflake",
        "connections",
        "ga4_properties",
        "gtm_containers",
        "ads_accounts",
        "search_console_sites",
    ):
        if hasattr(pctx, attr):
            setattr(user_ctx, attr, getattr(pctx, attr))


# ---------------------------------------------------------------------------
# /mcp endpoint — pure ASGI mount
#
# We deliberately do NOT use @app.api_route("/mcp", ...) for this endpoint.
# FastAPI's route wrapper expects the handler to RETURN a Response object
# and then calls ``await response(scope, receive, send)`` to serialize it.
# But the MCP SDK's ``session_manager.handle_request`` writes to the raw
# ASGI send() itself, so FastAPI's post-handler serialization double-sends
# and raises ``RuntimeError: Unexpected ASGI message 'http.response.start'
# sent, after response already completed``.
#
# The fix is to mount a raw ASGI app at /mcp so Starlette never tries to
# wrap the response. Auth and ContextVars are already set up by
# ``_FluxitoRequestMiddleware`` (scope["state"]) — this handler only
# needs to do the MCP dispatch + ContextVar lifecycle.
# ---------------------------------------------------------------------------


class _MCPASGIApp:
    """Callable class — makes Starlette treat it as raw ASGI (not a
    request-response handler), so it never wraps our response.
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return

        state = scope.get("state", {})
        user_ctx = state.get("user_context")
        if not user_ctx:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", b"Bearer"),
                        (b"content-type", b"application/json"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b'{"detail":"Unauthorized"}'})
            return

        # ── Demo viewer MCP block ────────────────────────────────────
        # When DEMO_VIEWER_EMAIL is set, that account can browse the
        # full web UI but cannot use the MCP endpoint.
        _demo_email = settings.DEMO_VIEWER_EMAIL
        if _demo_email and getattr(user_ctx, "email", "") == _demo_email:
            import json as _json

            body = _json.dumps(
                {
                    "error": True,
                    "error_type": "demo_restricted",
                    "message": (
                        "MCP / AI connections are disabled on the public demo. "
                        "Self-host Fluxito to use the full MCP experience."
                    ),
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        # ── Per-user MCP rate limiting (super-admins exempt) ─────────
        _uid = str(getattr(user_ctx, "user_id", "") or "")
        if _uid:
            from app.auth.superadmin_cache import is_superadmin_cached

            if not await is_superadmin_cached(_uid):
                from app.auth.rate_limiter import check_rate_limit

                blocked = await check_rate_limit(_uid)
                if blocked:
                    import json as _json

                    body = _json.dumps(blocked).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 429,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"retry-after", str(blocked.get("retry_after_seconds", 60)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return

        user_token = app_state.current_user_ctx.set(user_ctx)
        client_token = app_state.current_client_name_ctx.set(state.get("mcp_client_name"))
        project_token = app_state.current_project_ctx.set(None)
        try:
            await _apply_project_context(user_ctx)
            await mcp_server.session_manager.handle_request(scope, receive, send)
        finally:
            app_state.current_user_ctx.reset(user_token)
            app_state.current_client_name_ctx.reset(client_token)
            app_state.current_project_ctx.reset(project_token)


# Register via Starlette's Route. Because the endpoint is a CLASS INSTANCE
# with ``__call__`` (not a plain function), Starlette treats it as an
# ASGI app and skips the request-response wrapping that was causing the
# double-send error.
from starlette.routing import Route as _Route

app.router.routes.append(_Route("/mcp", endpoint=_MCPASGIApp(), methods=["GET", "POST", "DELETE"]))
