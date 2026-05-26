"""Shared utility helpers (HTTP, encryption, misc)."""

import uuid as _uuid

from starlette.requests import Request

DEFAULT_BASE_URL = "https://fluxito.ai"
LOCALHOST_INDICATORS = frozenset(["localhost", "127.", "::1"])


def safe_uuid(value: str | None) -> _uuid.UUID | None:
    """Parse a UUID string; return ``None`` instead of raising on bad input.

    Use at every route boundary where a UUID arrives from a path param,
    query string, request body, or cookie. Routes that called
    ``uuid.UUID(x)`` directly used to crash with HTTP 500 when the
    caller passed a malformed value — this returns ``None`` so the
    caller can answer 400/404 instead.
    """
    if not value:
        return None
    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def safe_next_url(value: str | None, default: str = "/home") -> str:
    """Validate a ``?next=`` redirect target; return ``default`` if unsafe.

    Accepts only same-origin paths starting with a single ``/`` — rejects
    protocol-relative (``//evil.com``), absolute (``http://...``),
    ``javascript:``, ``data:``, fragment-only (``#``), and empty values.
    Used in /signin, /auth/google/start, /auth/login to prevent open
    redirects via the ``next`` query parameter.
    """
    if not value or not isinstance(value, str):
        return default
    v = value.strip()
    if not v.startswith("/") or v.startswith("//"):
        return default
    # Reject backslash-prefixed paths (some browsers treat \\ like //)
    if v.startswith("/\\") or "\\" in v[:3]:
        return default
    return v


def base_url_from_request(request: Request) -> str:
    """Derive the public base URL from the incoming request.

    Priority: X-Forwarded-Proto + Host (reverse proxy) → localhost
    detection → settings.APP_BASE_URL → DEFAULT_BASE_URL. The result
    never has a trailing slash.
    """
    try:
        host = request.headers.get("host", "").strip()
        if host:
            fwd_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
            if fwd_proto in ("http", "https"):
                proto = fwd_proto
            elif any(indicator in host for indicator in LOCALHOST_INDICATORS):
                proto = "http"
            else:
                proto = "https"
            return f"{proto}://{host}"
    except Exception:
        pass

    try:
        from app.config import settings

        if getattr(settings, "APP_BASE_URL", None):
            return settings.APP_BASE_URL.rstrip("/")
    except Exception:
        pass

    return DEFAULT_BASE_URL
