"""
Credential Resolver for Card Script Execution

Resolves and decrypts OAuth credentials for a given user and platform,
returning them as environment variables for subprocess execution.

Security model:
  - Credentials are decrypted on-demand (Fernet key from settings)
  - Never written to disk, only passed as env vars
  - Freed from memory after subprocess completes
  - Each card script receives only the credentials it needs
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def resolve_credentials(
    user_id: str,
    platform: str,
    connection_id: str | None = None,
) -> dict[str, str]:
    """
    Resolve and decrypt credentials for a platform into env var names.
    Returns an empty dict if credentials cannot be resolved (card will show an error).
    """
    from cryptography.fernet import Fernet

    import app.app_state as state
    from app.config import settings

    fernet = Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())

    def _decrypt(val: str) -> str:
        try:
            return fernet.decrypt(val.encode()).decode()
        except Exception:
            return ""

    from sqlalchemy import select

    from app.models.connection import OAuthConnection

    if platform in ("ga4", "gtm", "google"):
        # Google OAuth — need access token + refresh token + client creds
        from app.auth.oauth_app_credentials import get_oauth_app_credentials

        async with state.db_session_factory() as db:
            q = select(OAuthConnection).where(
                OAuthConnection.user_id == user_id,
                OAuthConnection.provider == "google",
                OAuthConnection.is_active == True,
            )
            if connection_id:
                import uuid

                q = q.where(OAuthConnection.id == uuid.UUID(connection_id))
            q = q.limit(1)
            result = await db.execute(q)
            conn = result.scalar_one_or_none()

        if not conn:
            logger.warning("No Google connection found for user %s", user_id)
            return {}

        async with state.db_session_factory() as db:
            google_creds = await get_oauth_app_credentials(db, "google")

        env = {
            "AMCP_ACCESS_TOKEN": _decrypt(conn.access_token_encrypted),
            "AMCP_REFRESH_TOKEN": _decrypt(conn.refresh_token_encrypted),
            "AMCP_CLIENT_ID": google_creds.client_id,
            "AMCP_CLIENT_SECRET": google_creds.client_secret,
        }
        if platform == "google":
            env["AMCP_ADS_DEV_TOKEN"] = google_creds.extra.get("developer_token", "")

        return env

    if platform in ("meta", "tiktok", "snap"):
        provider_map = {"meta": "meta", "tiktok": "tiktok", "snap": "snap"}
        provider = provider_map[platform]

        async with state.db_session_factory() as db:
            result = await db.execute(
                select(OAuthConnection)
                .where(
                    OAuthConnection.user_id == user_id,
                    OAuthConnection.provider == provider,
                    OAuthConnection.is_active == True,
                )
                .limit(1)
            )
            conn = result.scalar_one_or_none()

        if not conn:
            logger.warning("No %s connection found for user %s", platform, user_id)
            return {}

        env = {"AMCP_ACCESS_TOKEN": _decrypt(conn.access_token_encrypted)}

        if platform == "meta":
            from app.auth.oauth_app_credentials import get_oauth_app_credentials

            async with state.db_session_factory() as db:
                meta_creds = await get_oauth_app_credentials(db, "meta")
            env["AMCP_META_APP_ID"] = meta_creds.client_id
            env["AMCP_META_APP_SECRET"] = meta_creds.client_secret

        return env

    if platform == "bigquery":
        from app.models.bq_connection import BQConnection

        async with state.db_session_factory() as db:
            result = await db.execute(
                select(BQConnection)
                .where(
                    BQConnection.user_id == user_id,
                    BQConnection.is_active == True,
                )
                .limit(1)
            )
            conn = result.scalar_one_or_none()

        if not conn:
            logger.warning("No BigQuery connection found for user %s", user_id)
            return {}

        sa_json = _decrypt(conn.service_account_encrypted)
        return {"AMCP_BQ_SA_JSON": sa_json}

    logger.warning("Unknown platform %s for credential resolution", platform)
    return {}
