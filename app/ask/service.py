"""Persistence for conversations + messages."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update

from app import app_state
from app.ask.providers.base import LLMMessage, blocks_from_json, blocks_to_json
from app.models.conversation import ChatMessage, Conversation


class ConversationService:
    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        provider: str,
        model: str,
        origin_section: str | None = None,
    ) -> Conversation:
        async with app_state.db_session_factory() as db:
            conv = Conversation(
                project_id=project_id,
                user_id=user_id,
                provider=provider,
                model=model,
                origin_section=origin_section,
            )
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
                            # Exclude phantom conversations that only anchor an
                            # Implement-hub deploy draft — they aren't real chats.
                            or_(
                                Conversation.origin_section.is_(None),
                                Conversation.origin_section != "implement",
                            ),
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
    ) -> uuid.UUID:
        """Persist one turn; returns the new ChatMessage id (so callers can
        link e.g. Flux drafts to the assistant message they were rendered under)."""
        async with app_state.db_session_factory() as db:
            next_seq = (
                await db.execute(
                    select(func.coalesce(func.max(ChatMessage.seq), -1) + 1).where(
                        ChatMessage.conversation_id == conversation_id
                    )
                )
            ).scalar_one()
            row = ChatMessage(
                conversation_id=conversation_id,
                role=message.role,
                seq=next_seq,
                content=blocks_to_json(message.content),
                token_usage=token_usage,
            )
            db.add(row)
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(last_message_at=func.now())
            )
            await db.commit()
            await db.refresh(row)
            return row.id

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
    ) -> list[tuple[uuid.UUID, LLMMessage, dict | None]]:
        """Like load_history but also returns the row id (for linking e.g. Flux
        drafts to the message they were attached to) and stored token_usage."""
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
                (r.id, LLMMessage(role=r.role, content=blocks_from_json(r.content)), r.token_usage)
                for r in rows
            ]

    async def find_block(self, conversation_id: uuid.UUID, block_id: str) -> dict | None:
        """Scan a conversation's persisted messages for a content block by id.

        Returns the raw JSONB block dict (e.g. a card_preview block), or None.
        """
        async with app_state.db_session_factory() as db:
            rows = (
                (await db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)))
                .scalars()
                .all()
            )
            for row in rows:
                for block in row.content or []:
                    if isinstance(block, dict) and block.get("id") == block_id:
                        return block
        return None

    async def set_block_state(self, conversation_id: uuid.UUID, block_id: str, state: str) -> bool:
        """Update a persisted content block's ``state`` field in place (e.g. card_preview's

        proposed -> added/discarded), rewriting the owning ChatMessage row. Returns True if a
        matching block was found and updated.
        """
        async with app_state.db_session_factory() as db:
            rows = (
                (await db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)))
                .scalars()
                .all()
            )
            for row in rows:
                found = False
                new_content: list[dict] = []
                for block in row.content or []:
                    if isinstance(block, dict) and block.get("id") == block_id:
                        block = {**block, "state": state}
                        found = True
                    new_content.append(block)
                if found:
                    row.content = new_content
                    await db.commit()
                    return True
        return False

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
