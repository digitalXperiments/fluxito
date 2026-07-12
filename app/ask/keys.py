"""Encrypted storage + retrieval of AI-vendor API keys.

Two scopes share one table (see AIProviderKey):
  - personal rows (``user_id`` set) — a member's own key;
  - project rows (``user_id IS NULL``) — a shared default set by an
    owner/admin that members fall back to when they have no personal key.
Every read helper resolves personal-first, then project."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update

from app import app_state
from app.models.conversation import AIProviderKey
from app.utils.encryption import decrypt_str, encrypt_str


def _user_clause(user_id: uuid.UUID | None):
    """WHERE fragment selecting one scope: a user's rows, or project rows."""
    return AIProviderKey.user_id.is_(None) if user_id is None else AIProviderKey.user_id == user_id


@dataclass
class ProviderKey:
    provider: str
    api_key: str
    default_model: str | None
    base_url: str | None = None


@dataclass
class ProviderKeyInfo:
    """Public info about a stored key — no secret."""

    provider: str
    default_model: str | None
    base_url: str | None
    is_default: bool
    scope: str = "personal"  # "personal" | "project"


async def store_key(
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None,
    provider: str,
    api_key: str,
    default_model: str | None,
    base_url: str | None = None,
) -> None:
    """Insert or replace the active key for (project, user, provider)."""
    async with app_state.db_session_factory() as db:
        # Check whether this provider was previously the default.
        was_default_row = (
            await db.execute(
                select(AIProviderKey.is_default).where(
                    AIProviderKey.project_id == project_id,
                    _user_clause(user_id),
                    AIProviderKey.provider == provider,
                    AIProviderKey.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        was_default = bool(was_default_row) if was_default_row is not None else False

        # Check whether this is the user's first active key in the project.
        existing_count = (
            (
                await db.execute(
                    select(AIProviderKey.id).where(
                        AIProviderKey.project_id == project_id,
                        _user_clause(user_id),
                        AIProviderKey.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Filter out the current provider's rows (they'll be deactivated).
        other_active = list(existing_count)
        is_first = len(other_active) == 0

        # Deactivate any existing active key for this triple.
        await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                _user_clause(user_id),
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
            )
            .values(is_active=False)
        )

        # The new key becomes default if: it was already default OR it's the first key.
        make_default = was_default or is_first

        db.add(
            AIProviderKey(
                project_id=project_id,
                user_id=user_id,
                provider=provider,
                api_key_encrypted=encrypt_str(api_key),
                default_model=default_model,
                base_url=base_url or None,
                is_active=True,
                is_default=make_default,
            )
        )
        await db.commit()


async def update_key_meta(
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None,
    provider: str,
    default_model: str | None,
    base_url: str | None,
) -> bool:
    """Update model/base_url on the active key without touching the encrypted secret.
    Returns True if a row was updated, False if no active key exists."""
    async with app_state.db_session_factory() as db:
        result = await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                _user_clause(user_id),
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
            )
            .values(default_model=default_model, base_url=base_url or None)
        )
        await db.commit()
        return result.rowcount > 0


async def get_active_key(
    *, project_id: uuid.UUID, user_id: uuid.UUID | None, provider: str
) -> ProviderKey | None:
    """Resolve a provider key: the user's personal key first, then the
    project-shared default (user_id IS NULL)."""
    async with app_state.db_session_factory() as db:
        scopes: list[uuid.UUID | None] = [user_id] if user_id is None else [user_id, None]
        for scope in scopes:
            row = (
                await db.execute(
                    select(AIProviderKey).where(
                        AIProviderKey.project_id == project_id,
                        _user_clause(scope),
                        AIProviderKey.provider == provider,
                        AIProviderKey.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return ProviderKey(
                    provider=row.provider,
                    api_key=decrypt_str(row.api_key_encrypted),
                    default_model=row.default_model,
                    base_url=row.base_url,
                )
        return None


async def list_keys(*, project_id: uuid.UUID, user_id: uuid.UUID | None) -> list[ProviderKeyInfo]:
    """Return public info for all active keys (no secrets)."""
    async with app_state.db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AIProviderKey).where(
                        AIProviderKey.project_id == project_id,
                        _user_clause(user_id),
                        AIProviderKey.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            ProviderKeyInfo(
                provider=row.provider,
                default_model=row.default_model,
                base_url=row.base_url,
                is_default=row.is_default,
                scope="project" if row.user_id is None else "personal",
            )
            for row in rows
        ]


async def list_effective_keys(*, project_id: uuid.UUID, user_id: uuid.UUID) -> list[ProviderKeyInfo]:
    """Personal keys plus project-shared keys the user has not overridden.

    A personal key wins over a project key for the same provider — mirroring
    get_active_key's resolution order."""
    personal = await list_keys(project_id=project_id, user_id=user_id)
    project = await list_keys(project_id=project_id, user_id=None)
    covered = {info.provider for info in personal}
    return personal + [info for info in project if info.provider not in covered]


async def list_providers(*, project_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    """Provider names that have a usable key — personal or project-shared."""
    infos = await list_effective_keys(project_id=project_id, user_id=user_id)
    return sorted({info.provider for info in infos})


async def delete_key(*, project_id: uuid.UUID, user_id: uuid.UUID | None, provider: str) -> None:
    """Deactivate the active key(s) for the given provider."""
    async with app_state.db_session_factory() as db:
        await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                _user_clause(user_id),
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
            )
            .values(is_active=False, is_default=False)
        )
        await db.commit()


async def set_default(*, project_id: uuid.UUID, user_id: uuid.UUID | None, provider: str) -> None:
    """Set is_default=True on the provider's active key; clear it on all others."""
    async with app_state.db_session_factory() as db:
        # Clear all defaults for this user+project.
        await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                _user_clause(user_id),
                AIProviderKey.is_active.is_(True),
            )
            .values(is_default=False)
        )
        # Set the chosen provider as default.
        await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                _user_clause(user_id),
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
            )
            .values(is_default=True)
        )
        await db.commit()


async def get_default_key(*, project_id: uuid.UUID, user_id: uuid.UUID | None) -> ProviderKey | None:
    """Return the default key; fall back to the most recently updated active
    key. Resolution: the user's own keys first, then the project-shared ones
    (user_id IS NULL)."""
    async with app_state.db_session_factory() as db:
        scopes: list[uuid.UUID | None] = [user_id] if user_id is None else [user_id, None]
        for scope in scopes:
            # Try is_default=True first within this scope.
            row = (
                await db.execute(
                    select(AIProviderKey).where(
                        AIProviderKey.project_id == project_id,
                        _user_clause(scope),
                        AIProviderKey.is_active.is_(True),
                        AIProviderKey.is_default.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                # Fall back to most recently updated in this scope.
                row = (
                    await db.execute(
                        select(AIProviderKey)
                        .where(
                            AIProviderKey.project_id == project_id,
                            _user_clause(scope),
                            AIProviderKey.is_active.is_(True),
                        )
                        .order_by(AIProviderKey.updated_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if row is not None:
                return ProviderKey(
                    provider=row.provider,
                    api_key=decrypt_str(row.api_key_encrypted),
                    default_model=row.default_model,
                    base_url=row.base_url,
                )
        return None
