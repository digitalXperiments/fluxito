"""Origins for hosted web dashboards.

Untrusted artifact JS must never run on the Fluxito app origin. The dash
host (DASHBOARD_ORIGIN, default localhost:8002 or dash.<app-host>) serves
static files + POST /query only. Viewer cookies stay on the app origin.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request

from app.config import settings

DASH_SURFACE_HEADER = "x-fluxito-surface"
DASH_SURFACE_VALUE = "dash"
DEFAULT_LOCAL_DASH_PORT = 8002


def _origin(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def app_origin() -> str:
    return _origin(settings.APP_BASE_URL) or "http://localhost:8000"


def dashboard_origin() -> str:
    explicit = (getattr(settings, "DASHBOARD_ORIGIN", None) or "").strip()
    if explicit:
        return _origin(explicit) or explicit.rstrip("/")
    parsed = urlparse(settings.APP_BASE_URL or "http://localhost:8000")
    host = (parsed.hostname or "localhost").lower()
    scheme = parsed.scheme or "http"
    if host in {"localhost", "127.0.0.1"}:
        return f"{scheme}://{host}:{DEFAULT_LOCAL_DASH_PORT}"
    if host.startswith("dash."):
        return f"{scheme}://{parsed.netloc}".rstrip("/")
    netloc = parsed.netloc
    if host and netloc.lower().startswith(host):
        netloc = "dash." + netloc
    else:
        netloc = f"dash.{host}"
    return f"{scheme}://{netloc}".rstrip("/")


def origins_are_isolated() -> bool:
    return app_origin() != dashboard_origin()


def dash_src(slug: str, path: str = "") -> str:
    base = dashboard_origin().rstrip("/")
    rest = path.lstrip("/")
    if rest:
        return f"{base}/s/{slug}/{rest}"
    return f"{base}/s/{slug}/"


def request_host(request: Request) -> str:
    return (request.headers.get("host") or request.url.netloc or "").split("%")[0]


def is_dash_request(request: Request) -> bool:
    if (request.headers.get(DASH_SURFACE_HEADER) or "").strip().lower() == DASH_SURFACE_VALUE:
        return True
    host = request_host(request).lower()
    dash = urlparse(dashboard_origin())
    dash_host = (dash.hostname or "").lower()
    dash_port = dash.port
    req_host = host.split(":")[0].lower()
    req_port: int | None = None
    if ":" in host:
        try:
            req_port = int(host.rsplit(":", 1)[1])
        except ValueError:
            req_port = None
    elif request.url.port:
        req_port = request.url.port
    if dash_host and req_host == dash_host:
        if dash_port is None or req_port is None or req_port == dash_port:
            return True
    return req_port == DEFAULT_LOCAL_DASH_PORT


def dash_path_allowed(method: str, path: str) -> bool:
    method = method.upper()
    if path == "/dash-health":
        return method in {"GET", "HEAD"}
    if path == "/fluxito.js":
        return method in {"GET", "HEAD"}
    if path == "/query":
        return method in {"POST", "OPTIONS"}
    if path == "/s" or path.startswith("/s/"):
        return method in {"GET", "HEAD"}
    return False


def is_dash_only_path(path: str) -> bool:
    return (
        path == "/dash-health"
        or path == "/fluxito.js"
        or path == "/query"
        or path == "/s"
        or path.startswith("/s/")
    )


def content_security_policy() -> str:
    parent = app_origin()
    extras = ["https://dev.fluxito.app"]
    if "localhost" in parent or "127.0.0.1" in parent:
        extras.extend(["http://127.0.0.1:8000", "http://localhost:8010"])
    ancestors = " ".join(dict.fromkeys([parent, *extras]))
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        f"frame-ancestors {ancestors}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
