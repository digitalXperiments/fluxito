"""
Shared helpers for MCP tool modules.

Centralizes common patterns used across marketing_tools, cross_platform_tools,
template_tools, analytics_tools, and warehouse_tools to eliminate duplication.

All data-access helpers resolve connections from ProjectContext first (project-
scoped), falling back to UserContext only when no active project is set.
"""

from typing import Any

import app.app_state as state
from app.config import settings

# ---------------------------------------------------------------------------
# User / project / connection helpers
# ---------------------------------------------------------------------------


def get_current_user() -> Any | None:
    """Return the current MCP user context, or None."""
    return state.current_user_ctx.get()


def get_current_project() -> Any | None:
    """Return the current MCP project context, or None."""
    return state.current_project_ctx.get()


def get_google_conn_id() -> str | None:
    """
    Return the Google OAuth connection_id scoped to the active project.
    Falls back to user-level connections only if no project is active.
    """
    # Prefer project-scoped connections
    project = get_current_project()
    connections = None
    if project and project.connections:
        connections = project.connections
    else:
        user = get_current_user()
        if user and user.connections:
            connections = user.connections

    if not connections:
        return None

    for conn in connections:
        if getattr(conn, "provider", "google") in ("google", "", None):
            return conn.id
    return connections[0].id


def get_provider_token(provider_str: str) -> str | None:
    """
    Return the decrypted access token for a non-Google OAuth provider
    (meta, tiktok, snap) stored in OAuthConnection rows.
    Scoped to the active project first; falls back to user context.
    Returns None if no connection exists for this provider.
    """
    # Prefer project-scoped connections
    project = get_current_project()
    connections = None
    if project and project.connections:
        connections = project.connections
    else:
        user = get_current_user()
        if user and user.connections:
            connections = user.connections

    if not connections:
        return None

    for conn in connections:
        if getattr(conn, "provider", "") == provider_str:
            try:
                return state.token_manager.decrypt(conn.access_token_encrypted)
            except Exception:
                return None
    return None


def get_provider_oauth1_tokens(provider_str: str) -> tuple[str, str] | None:
    """Return decrypted OAuth 1.0a access token and token secret for a provider."""
    project = get_current_project()
    connections = None
    if project and project.connections:
        connections = project.connections
    else:
        user = get_current_user()
        if user and user.connections:
            connections = user.connections

    if not connections:
        return None

    for conn in connections:
        if getattr(conn, "provider", "") == provider_str:
            try:
                return (
                    state.token_manager.decrypt(conn.access_token_encrypted),
                    state.token_manager.decrypt(conn.refresh_token_encrypted),
                )
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# "No connection" response factory
# ---------------------------------------------------------------------------


def no_connection_response(platform: str, connect_path: str, message: str) -> dict[str, Any]:
    """Build a standard 'connection_missing' error response."""
    base_url = settings.APP_BASE_URL
    connect_url = f"{base_url}{connect_path}"
    return {
        "error": True,
        "error_type": "connection_missing",
        "message": message,
        "connect_url": connect_url,
        "action_required": f"Visit {connect_url} to connect.",
    }


# ---------------------------------------------------------------------------
# Credential fetcher (encrypted DB credentials) — project-scoped
# ---------------------------------------------------------------------------


def _resolve_project_id() -> str | None:
    """Return the active project_id string, or None."""
    project = get_current_project()
    return project.project_id if project else None


async def get_encrypted_credential_conn(
    model_class: type,
    user_id: str,
    connection_id: str | None = None,
    extra_filters: list[Any] | None = None,
) -> Any | None:
    """
    Generic fetcher for credential-based connections (Amplitude, Adobe,
    Redshift, Snowflake, BigQuery).

    Scopes to the active project when available; falls back to user_id.
    Returns the ORM row or None.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.bq_connection import BQConnection

    db_session = state.db_session_factory()
    project_id = _resolve_project_id()

    async with db_session as db:
        stmt = select(model_class).where(model_class.is_active == True)

        # Project-scoped when active project exists
        if project_id:
            pid = _uuid.UUID(project_id)
            # BQConnection uses fluxito_project_id instead of project_id
            if model_class is BQConnection:
                stmt = stmt.where(model_class.fluxito_project_id == pid)
            else:
                stmt = stmt.where(model_class.project_id == pid)
        else:
            # Fallback to user-scoped (no active project)
            stmt = stmt.where(model_class.user_id == _uuid.UUID(user_id))

        if connection_id:
            stmt = stmt.where(model_class.id == _uuid.UUID(connection_id))
        if extra_filters:
            for f in extra_filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def list_active_connections(model_class: type, user_id: str) -> list[Any]:
    """
    List all active connections of a given type.
    Scoped to the active project when available; falls back to user_id.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.bq_connection import BQConnection

    db_session = state.db_session_factory()
    project_id = _resolve_project_id()

    async with db_session as db:
        stmt = select(model_class).where(model_class.is_active == True)

        if project_id:
            pid = _uuid.UUID(project_id)
            if model_class is BQConnection:
                stmt = stmt.where(model_class.fluxito_project_id == pid)
            else:
                stmt = stmt.where(model_class.project_id == pid)
        else:
            stmt = stmt.where(model_class.user_id == _uuid.UUID(user_id))

        result = await db.execute(stmt)
        return result.scalars().all()


def decrypt_field(encrypted_value: str) -> str:
    """Decrypt a Fernet-encrypted field using the configured encryption key."""
    from cryptography.fernet import Fernet

    f = Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())
    return f.decrypt(encrypted_value.encode()).decode()
