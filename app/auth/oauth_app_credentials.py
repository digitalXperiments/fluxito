"""OAuth app credential lookup + CRUD service.

`get_oauth_app_credentials(db, platform)` is the canonical read API used
by every connector and route that needs an OAuth client ID/secret.
Credentials are stored exclusively in the `oauth_app_credentials` DB table,
configured via /settings/integrations (admin access required).

Decryption uses ``TOKEN_ENCRYPTION_KEY`` (same Fernet key as user
OAuth tokens). Plaintext is only held in memory; never logged.

A 5-minute in-memory cache fronts the DB read so token-refresh
hot paths don't hit Postgres on every call. The cache is invalidated
on `upsert_oauth_app_credentials` / `delete_oauth_app_credentials`
within the same Python process; multi-worker installs converge
within ``_CACHE_TTL_SEC`` seconds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_app_credential import SUPPORTED_PLATFORMS, OAuthAppCredential
from app.utils.encryption import decrypt_str, encrypt_str


class OAuthAppNotConfigured(Exception):
    """Raised when a platform has no DB row configured."""


@dataclass(frozen=True)
class OAuthAppCreds:
    """Resolved OAuth app credentials for a platform.

    `source` is 'db' — kept as a named field for forward-compatibility
    (e.g. a future secrets-manager source). Its only valid value today is 'db'.
    """

    platform: str
    client_id: str
    client_secret: str
    extra: dict[str, Any]
    source: str


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, OAuthAppCreds]] = {}
_CACHE_TTL_SEC = 300


def _cache_get(platform: str) -> OAuthAppCreds | None:
    entry = _CACHE.get(platform)
    if entry is None:
        return None
    ts, creds = entry
    if time.monotonic() - ts >= _CACHE_TTL_SEC:
        _CACHE.pop(platform, None)
        return None
    return creds


def _cache_put(platform: str, creds: OAuthAppCreds) -> None:
    _CACHE[platform] = (time.monotonic(), creds)


def _cache_invalidate(platform: str) -> None:
    _CACHE.pop(platform, None)


def _cache_clear_all() -> None:
    """Clear the entire cache. Used by tests."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Read APIs
# ---------------------------------------------------------------------------


async def get_oauth_app_credentials(db: AsyncSession, platform: str) -> OAuthAppCreds:
    """Return OAuth credentials for `platform` from the DB.

    Resolution order:
      1. Row in `oauth_app_credentials` → decrypt and return (source='db').
      2. Raise `OAuthAppNotConfigured`.

    This call is **uncached** — every invocation hits the DB. For hot paths
    (e.g. token refresh on every tool call), use `get_oauth_app_credentials_cached`.
    """
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}")

    row = (
        await db.execute(select(OAuthAppCredential).where(OAuthAppCredential.platform == platform))
    ).scalar_one_or_none()

    if row is not None:
        return OAuthAppCreds(
            platform=platform,
            client_id=row.client_id,
            client_secret=decrypt_str(
                row.client_secret.decode() if isinstance(row.client_secret, bytes) else row.client_secret
            ),
            extra=dict(row.extra_json) if row.extra_json else {},
            source="db",
        )

    raise OAuthAppNotConfigured(
        f"OAuth app for {platform!r} is not configured. "
        f"Configure it at /settings/integrations (admin access required)."
    )


async def get_oauth_app_credentials_cached(db: AsyncSession, platform: str) -> OAuthAppCreds:
    """Cached variant of `get_oauth_app_credentials` (5-minute TTL).

    Use on hot paths like token refresh. Cache invalidated on upsert/delete
    within the same process; multi-worker installs converge within
    `_CACHE_TTL_SEC` seconds of an update.
    """
    cached = _cache_get(platform)
    if cached is not None:
        return cached
    creds = await get_oauth_app_credentials(db, platform)
    _cache_put(platform, creds)
    return creds


async def list_oauth_app_status(db: AsyncSession) -> list[dict[str, Any]]:
    """Return one entry per supported platform.

    Each entry has: platform, source ('db' | 'unconfigured'),
    client_id_masked (None when unconfigured), updated_at (only for DB rows).
    Used by the settings UI to render the platform grid.
    """
    rows = (await db.execute(select(OAuthAppCredential))).scalars().all()
    db_map = {r.platform: r for r in rows}

    out: list[dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        if platform in db_map:
            r = db_map[platform]
            out.append(
                {
                    "platform": platform,
                    "source": "db",
                    "client_id_masked": _mask(r.client_id),
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
            )
        else:
            out.append(
                {
                    "platform": platform,
                    "source": "unconfigured",
                    "client_id_masked": None,
                    "updated_at": None,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Write APIs
# ---------------------------------------------------------------------------


async def upsert_oauth_app_credentials(
    db: AsyncSession,
    *,
    platform: str,
    client_id: str,
    client_secret: str,
    extra: dict[str, Any] | None,
    configured_by_user_id,
) -> OAuthAppCredential:
    """Insert or update the row for a platform. Caller commits the session."""
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}")
    if not client_id or not client_secret:
        raise ValueError("client_id and client_secret are required")

    row = (
        await db.execute(select(OAuthAppCredential).where(OAuthAppCredential.platform == platform))
    ).scalar_one_or_none()

    if row is None:
        row = OAuthAppCredential(
            platform=platform,
            client_id=client_id,
            client_secret=encrypt_str(client_secret).encode(),
            extra_json=(extra or None),
            configured_by_user_id=configured_by_user_id,
        )
        db.add(row)
    else:
        row.client_id = client_id
        row.client_secret = encrypt_str(client_secret).encode()
        row.extra_json = extra or None
        row.configured_by_user_id = configured_by_user_id

    _cache_invalidate(platform)
    return row


async def delete_oauth_app_credentials(db: AsyncSession, *, platform: str) -> bool:
    """Delete the row for a platform. Caller commits. Returns True if deleted."""
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}")

    row = (
        await db.execute(select(OAuthAppCredential).where(OAuthAppCredential.platform == platform))
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    _cache_invalidate(platform)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask(client_id: str) -> str:
    """Mask a client_id for display: keep first 6 + last 4."""
    if not client_id:
        return ""
    if len(client_id) <= 12:
        return "***"
    return f"{client_id[:6]}…{client_id[-4:]}"
