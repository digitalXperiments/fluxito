"""General application settings service (DB-backed, with optional encryption).

This is the canonical read/write API for the `app_settings` table.

Design goals (matching the quality bar of oauth_app_credentials.py):
- Simple key/value storage with JSONB values.
- Optional Fernet encryption for secrets (reuses the same TOKEN_ENCRYPTION_KEY).
- 5-minute in-memory TTL cache for hot paths.
- Clear separation between public API and internal helpers.

Usage example:
    from app.settings_service import get_setting, set_setting

    smtp_host = await get_setting(db, "smtp_host")
    await set_setting(db, "smtp_host", "mail.example.com", is_secret=False, ...)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.app_setting import AppSetting
from app.utils.encryption import decrypt_str, encrypt_str


@dataclass(frozen=True)
class RuntimeSetting:
    key: str
    label: str
    description: str
    env_name: str
    value_type: str = "str"
    is_secret: bool = False
    category: str = "general"


RUNTIME_SETTINGS: tuple[RuntimeSetting, ...] = (
    # ── Email / SMTP ──
    RuntimeSetting("smtp_host", "SMTP host", "Host name for outbound email.", "SMTP_HOST", category="email"),
    RuntimeSetting(
        "smtp_port", "SMTP port", "SMTP port for outbound email.", "SMTP_PORT", "int", category="email"
    ),
    RuntimeSetting(
        "smtp_username", "SMTP username", "Optional SMTP username.", "SMTP_USERNAME", category="email"
    ),
    RuntimeSetting(
        "smtp_password",
        "SMTP password",
        "Optional SMTP password.",
        "SMTP_PASSWORD",
        is_secret=True,
        category="email",
    ),
    RuntimeSetting(
        "smtp_from_email", "From address", "Sender email address.", "SMTP_FROM_EMAIL", category="email"
    ),
    RuntimeSetting("smtp_from_name", "From name", "Sender display name.", "SMTP_FROM_NAME", category="email"),
    # ── Rate Limiting ──
    RuntimeSetting(
        "rate_limit_per_min",
        "Requests per minute",
        "Default API requests per user per minute.",
        "RATE_LIMIT_PER_MIN",
        "int",
        category="rate_limiting",
    ),
    RuntimeSetting(
        "rate_limit_per_hour",
        "Requests per hour",
        "Default API requests per user per hour.",
        "RATE_LIMIT_PER_HOUR",
        "int",
        category="rate_limiting",
    ),
    # ── Observability ──
    RuntimeSetting(
        "sentry_dsn",
        "Sentry DSN",
        "Optional Sentry project DSN.",
        "SENTRY_DSN",
        is_secret=True,
        category="observability",
    ),
    RuntimeSetting(
        "sentry_traces_sample_rate",
        "Trace sample rate",
        "Fraction of requests to trace (0.0 – 1.0).",
        "SENTRY_TRACES_SAMPLE_RATE",
        "float",
        category="observability",
    ),
    # ── Storage & Platform ──
    RuntimeSetting(
        "cors_allowed_origins",
        "CORS allowed origins",
        "Comma-separated origins for browser API access.",
        "CORS_ALLOWED_ORIGINS",
        category="platform",
    ),
    RuntimeSetting(
        "gcs_bucket_name",
        "GCS bucket name",
        "Bucket for dashboard artifacts.",
        "GCS_BUCKET_NAME",
        category="platform",
    ),
    RuntimeSetting(
        "gcs_service_account_json",
        "GCS service account JSON",
        "Optional service account JSON; ADC is used when blank.",
        "GCS_SERVICE_ACCOUNT_JSON",
        is_secret=True,
        category="platform",
    ),
    RuntimeSetting(
        "dashboards_local_dir",
        "Local dashboard directory",
        "Local dashboard artifact directory.",
        "DASHBOARDS_LOCAL_DIR",
        category="platform",
    ),
    RuntimeSetting(
        "enabled_tools",
        "Enabled tools",
        "Comma-separated tool domains or 'all'.",
        "ENABLED_TOOLS",
        category="platform",
    ),
)

RUNTIME_SETTING_BY_KEY = {item.key: item for item in RUNTIME_SETTINGS}


# ---------------------------------------------------------------------------
# In-memory TTL cache (same TTL as the OAuth credential cache)
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SEC = 300


def _cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts >= _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    _CACHE[key] = (time.monotonic(), value)


def _cache_invalidate(key: str) -> None:
    _CACHE.pop(key, None)


def _cache_clear_all() -> None:
    """Clear the entire cache. Used by tests."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_setting(db: AsyncSession, key: str, *, default: Any = None) -> Any:
    """
    Return the value for `key` from the DB (decrypted if it was stored as a secret).

    Returns `default` if the key does not exist.
    """
    cached = _cache_get(key)
    if cached is not None:
        return cached

    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        return default

    if row.is_secret and row.value_json is not None:
        # We store secrets as {"enc": "<base64>"} so we can distinguish them
        enc = row.value_json.get("enc")
        if enc:
            try:
                value = decrypt_str(enc)
                _cache_put(key, value)
                return value
            except Exception:
                # Corrupt or wrong key — treat as missing
                return default

    value = row.value_json.get("value") if row.value_json else None
    _cache_put(key, value)
    return value


async def set_setting(
    db: AsyncSession,
    *,
    key: str,
    value: Any,
    is_secret: bool = False,
    updated_by_user_id=None,
) -> AppSetting:
    """
    Insert or update a setting. If `is_secret` is True the value is encrypted
    before storage. Caller is responsible for committing the session.
    """
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()

    if is_secret:
        payload = {"enc": encrypt_str(str(value))}
    else:
        payload = {"value": value}

    if row is None:
        row = AppSetting(
            key=key,
            value_json=payload,
            is_secret=is_secret,
            updated_by_user_id=updated_by_user_id,
        )
        db.add(row)
    else:
        row.value_json = payload
        row.is_secret = is_secret
        row.updated_by_user_id = updated_by_user_id

    _cache_invalidate(key)
    return row


def _coerce_value(raw: Any, value_type: str) -> Any:
    if raw is None:
        return None
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return raw


async def get_runtime_setting(db: AsyncSession, key: str, *, default: Any = None) -> Any:
    """Return a runtime setting from DB first, then the deprecated env fallback."""
    spec = RUNTIME_SETTING_BY_KEY.get(key)
    db_value = await get_setting(db, key, default=None)
    if db_value not in (None, ""):
        return _coerce_value(db_value, spec.value_type if spec else "str")
    if spec:
        env_default = getattr(settings, spec.env_name, default)
        return _coerce_value(env_default, spec.value_type)
    return default


async def list_settings(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all settings (secrets are returned masked). Used by admin UI."""
    rows = (await db.execute(select(AppSetting))).scalars().all()
    out = []
    for r in rows:
        masked = "********" if r.is_secret else (r.value_json.get("value") if r.value_json else None)
        out.append(
            {
                "key": r.key,
                "value": masked,
                "is_secret": r.is_secret,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return out


async def list_runtime_settings(db: AsyncSession) -> list[dict[str, Any]]:
    """Return the curated settings registry with DB/env/default status."""
    rows = (await db.execute(select(AppSetting))).scalars().all()
    db_map = {r.key: r for r in rows}
    out: list[dict[str, Any]] = []
    for spec in RUNTIME_SETTINGS:
        row = db_map.get(spec.key)
        if row is None:
            fallback = getattr(settings, spec.env_name, "")
            out.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "description": spec.description,
                    "value": "********" if spec.is_secret and fallback else fallback,
                    "value_type": spec.value_type,
                    "is_secret": spec.is_secret,
                    "category": spec.category,
                    "source": "env/default",
                    "updated_at": None,
                }
            )
            continue
        value = "********" if row.is_secret else (row.value_json.get("value") if row.value_json else None)
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "value": value,
                "value_type": spec.value_type,
                "is_secret": spec.is_secret,
                "category": spec.category,
                "source": "db",
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return out


async def delete_setting(db: AsyncSession, *, key: str) -> bool:
    """Delete a setting. Returns True if a row was deleted. Caller commits."""
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    _cache_invalidate(key)
    return True
