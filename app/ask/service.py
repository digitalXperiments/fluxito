"""Persistence for conversations + messages."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update

from app import app_state
from app.ask.providers.base import LLMMessage, blocks_from_json, blocks_to_json
from app.models.conversation import ChatMessage, Conversation


class ConversationService:
    async def create(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, provider: str, model: str
    ) -> Conversation:
        async with app_state.db_session_factory() as db:
            conv = Conversation(project_id=project_id, user_id=user_id, provider=provider, model=model)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
            return conv

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        async with app_state.db_session_factory() as db:
            return (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalar_one_or_none()

    async def list_for(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
    ) -> list[Conversation]:
        async with app_state.db_session_factory() as db:
            return list(
                (
                    await db.execute(
                        select(Conversation)
                        .where(
                            Conversation.project_id == project_id,
                            Conversation.user_id == user_id,
                            Conversation.archived.is_(False),
                        )
                        .order_by(Conversation.last_message_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

    async def append(
        self,
        conversation_id: uuid.UUID,
        message: LLMMessage,
        *,
        token_usage: dict | None = None,
    ) -> None:
        async with app_state.db_session_factory() as db:
            next_seq = (
                await db.execute(
                    select(func.coalesce(func.max(ChatMessage.seq), -1) + 1).where(
                        ChatMessage.conversation_id == conversation_id
                    )
                )
            ).scalar_one()
            db.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    role=message.role,
                    seq=next_seq,
                    content=blocks_to_json(message.content),
                    token_usage=token_usage,
                )
            )
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(last_message_at=func.now())
            )
            await db.commit()

    async def load_history(self, conversation_id: uuid.UUID) -> list[LLMMessage]:
        async with app_state.db_session_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.conversation_id == conversation_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [LLMMessage(role=r.role, content=blocks_from_json(r.content)) for r in rows]

    async def load_history_with_usage(
        self, conversation_id: uuid.UUID
    ) -> list[tuple[LLMMessage, dict | None]]:
        """Like load_history but also returns the stored token_usage per row."""
        async with app_state.db_session_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.conversation_id == conversation_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                (LLMMessage(role=r.role, content=blocks_from_json(r.content)), r.token_usage) for r in rows
            ]

    async def set_title(self, conversation_id: uuid.UUID, title: str) -> None:
        async with app_state.db_session_factory() as db:
            await db.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(title=title[:200])
            )
            await db.commit()

    async def archive(self, conversation_id: uuid.UUID) -> None:
        async with app_state.db_session_factory() as db:
            await db.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(archived=True)
            )
            await db.commit()
