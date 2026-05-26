"""
CSRF protection via double-submit cookie + custom header.

Strategy:
  1. A middleware sets a signed CSRF cookie on every response (if missing).
  2. All state-changing requests (POST/PATCH/PUT/DELETE) to non-API/non-MCP
     paths must include an X-CSRF-Token header matching the cookie value.
  3. The admin/billing/etc. JS already uses fetch() with JSON — the
     base template injects a tiny JS snippet that reads the cookie and
     attaches the header to all fetch() calls automatically.

Exempt paths:
  - /mcp/* — MCP uses Bearer tokens; no browser cookies involved
  - /oauth/* — OAuth flows use state params for CSRF
  - /auth/google/* — Google OAuth callbacks use state params
  - Static files and GET requests (safe methods)

This approach requires zero changes to individual route handlers.
"""

import hashlib
import hmac
import logging
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

# CSRF configuration constants
_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"
_CSRF_TOKEN_SEPARATOR = "."
_CSRF_SIGNATURE_LENGTH = 16
_CSRF_RAW_TOKEN_BYTES = 32
_CSRF_COOKIE_MAX_AGE = 86400  # 24 hours
_CSRF_HASH_ALGO = hashlib.sha256
_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS", "TRACE"))

# Paths exempt from CSRF validation
_EXEMPT_PREFIXES = (
    "/mcp",  # Streamable HTTP endpoint — matches /mcp and any /mcp/*
    "/oauth/",
    "/api/",  # JSON API — CORS + Content-Type:application/json already prevents CSRF
    "/auth/google/",
    "/static/",
    "/.well-known/",
    "/setup",  # First-run setup — no authenticated session exists yet
)


def _generate_csrf_token() -> str:
    """Generate a random CSRF token and sign it with the app secret."""
    raw = secrets.token_hex(_CSRF_RAW_TOKEN_BYTES)
    sig = hmac.new(settings.APP_SECRET_KEY.encode(), raw.encode(), _CSRF_HASH_ALGO).hexdigest()[
        :_CSRF_SIGNATURE_LENGTH
    ]
    return f"{raw}{_CSRF_TOKEN_SEPARATOR}{sig}"


def _verify_csrf_token(token: str) -> bool:
    """Verify the CSRF token signature using constant-time comparison."""
    try:
        raw, sig = token.rsplit(_CSRF_TOKEN_SEPARATOR, 1)
        expected = hmac.new(settings.APP_SECRET_KEY.encode(), raw.encode(), _CSRF_HASH_ALGO).hexdigest()[
            :_CSRF_SIGNATURE_LENGTH
        ]
        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(sig, expected)
    except (ValueError, AttributeError, TypeError):
        return False


def csrf_precheck_asgi(scope) -> "JSONResponse | None":
    """Pure-ASGI CSRF pre-check — returns a 403 JSONResponse or None.

    Used by ``_FluxitoRequestMiddleware`` in main.py. Unlike
    ``csrf_middleware`` (BaseHTTPMiddleware-flavored, buffers body),
    this function only validates the INCOMING request and never
    touches the outgoing response, so it's safe to run in front of
    streaming endpoints like /mcp.

    The ``_ensure_csrf_cookie`` response-side set-cookie logic is
    handled separately by a post-response hook (or we accept that
    cookies are only set on the first page load — CSRF still works
    since the token is long-lived in localStorage after that).
    """
    from starlette.requests import Request as _StarletteRequest

    request = _StarletteRequest(scope)
    method = request.method
    path = request.url.path

    if method in _SAFE_METHODS or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return None

    cookie_token = request.cookies.get(_CSRF_COOKIE_NAME)
    header_token = request.headers.get(_CSRF_HEADER_NAME)
    client_ip = _client_ip(request)

    if not cookie_token or not header_token:
        logger.warning(
            "CSRF validation failed: missing %s (path=%s, ip=%s)",
            "cookie" if not cookie_token else "header",
            path,
            client_ip,
        )
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "CSRF token missing. Please refresh the page and try again.",
            },
            status_code=403,
        )
    if not hmac.compare_digest(cookie_token, header_token):
        logger.warning("CSRF validation failed: token mismatch (path=%s, ip=%s)", path, client_ip)
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "CSRF token mismatch. Please refresh the page and try again.",
            },
            status_code=403,
        )
    if not _verify_csrf_token(cookie_token):
        logger.warning("CSRF validation failed: invalid signature (path=%s, ip=%s)", path, client_ip)
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "Invalid CSRF token. Please refresh the page and try again.",
            },
            status_code=403,
        )
    return None


async def csrf_middleware(request: Request, call_next) -> Response:
    """
    CSRF double-submit cookie middleware.

    Safe methods and exempt paths pass through.
    State-changing requests must have X-CSRF-Token header matching the cookie.
    """
    method = request.method
    path = request.url.path

    # Safe methods and exempt paths — skip validation
    if method in _SAFE_METHODS or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        response = await call_next(request)
        _ensure_csrf_cookie(request, response)
        return response

    # State-changing request — validate CSRF token
    cookie_token = request.cookies.get(_CSRF_COOKIE_NAME)
    header_token = request.headers.get(_CSRF_HEADER_NAME)
    client_ip = _client_ip(request)

    if not cookie_token or not header_token:
        logger.warning(
            "CSRF validation failed: missing %s (path=%s, ip=%s)",
            "cookie" if not cookie_token else "header",
            path,
            client_ip,
        )
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "CSRF token missing. Please refresh the page and try again.",
            },
            status_code=403,
        )

    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(cookie_token, header_token):
        logger.warning(
            "CSRF validation failed: token mismatch (path=%s, ip=%s)",
            path,
            client_ip,
        )
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "CSRF token mismatch. Please refresh the page and try again.",
            },
            status_code=403,
        )

    if not _verify_csrf_token(cookie_token):
        logger.warning("CSRF validation failed: invalid signature (path=%s, ip=%s)", path, client_ip)
        return JSONResponse(
            {
                "error": True,
                "error_type": "csrf_error",
                "message": "Invalid CSRF token. Please refresh the page and try again.",
            },
            status_code=403,
        )

    response = await call_next(request)
    return response


def _ensure_csrf_cookie(request: Request, response: Response) -> None:
    """Set the CSRF cookie if it's not already present."""
    if _CSRF_COOKIE_NAME not in request.cookies:
        token = _generate_csrf_token()
        response.set_cookie(
            key=_CSRF_COOKIE_NAME,
            value=token,
            httponly=False,  # Must be readable by JS
            samesite="lax",
            secure=settings.APP_ENV == "production",
            max_age=_CSRF_COOKIE_MAX_AGE,
            path="/",
        )


def _client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
