"""Encrypted storage + retrieval of AI-vendor API keys."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update

from app import app_state
from app.models.conversation import AIProviderKey
from app.utils.encryption import decrypt_str, encrypt_str


@dataclass
class ProviderKey:
    provider: str
    api_key: str
    default_model: str | None


async def store_key(
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: str,
    api_key: str,
    default_model: str | None,
) -> None:
    """Insert or replace the active key for (project, user, provider)."""
    async with app_state.db_session_factory() as db:
        # Deactivate any existing active key for this triple.
        await db.execute(
            update(AIProviderKey)
            .where(
                AIProviderKey.project_id == project_id,
                AIProviderKey.user_id == user_id,
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
            )
            .values(is_active=False)
        )
        db.add(
            AIProviderKey(
                project_id=project_id,
                user_id=user_id,
                provider=provider,
                api_key_encrypted=encrypt_str(api_key),
                default_model=default_model,
                is_active=True,
            )
        )
        await db.commit()


async def get_active_key(*, project_id: uuid.UUID, user_id: uuid.UUID, provider: str) -> ProviderKey | None:
    async with app_state.db_session_factory() as db:
        row = (
            await db.execute(
                select(AIProviderKey).where(
                    AIProviderKey.project_id == project_id,
                    AIProviderKey.user_id == user_id,
                    AIProviderKey.provider == provider,
                    AIProviderKey.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return ProviderKey(
            provider=row.provider,
            api_key=decrypt_str(row.api_key_encrypted),
            default_model=row.default_model,
        )


async def list_providers(*, project_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    """Provider names that have an active key (for the settings UI)."""
    async with app_state.db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AIProviderKey.provider).where(
                        AIProviderKey.project_id == project_id,
                        AIProviderKey.user_id == user_id,
                        AIProviderKey.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        return sorted(set(rows))
