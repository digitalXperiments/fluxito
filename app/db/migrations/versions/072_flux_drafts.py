"""072 — flux_drafts: Flux-drafted changes awaiting human approval.

Backs the Conversation "approve flow" (Ledger revamp Phase 1.1): when Flux
drafts a concrete change (e.g. a GTM workspace edit), it is persisted here as
`pending` and rendered as a diff card in the chat thread. Approving marks it
`published` (and, once a real write path is wired up, publishes the
underlying change — see app/ask/drafts.py); rejecting marks it `rejected`.
Both actions are logged to `activity_events`.

Additive: one new table. Reversible.

Revision ID: 072_flux_drafts
Revises: 071_user_flux_prefs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "072_flux_drafts"
down_revision = "071_user_flux_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flux_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The assistant chat_messages row whose stream carried the draft
        # marker, so a reloaded conversation can re-attach the card to the
        # right spot in the thread. Nullable + SET NULL: the draft itself is
        # the source of truth for status even if the message is pruned.
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 'gtm_workspace_change' today; open-ended for future draft kinds.
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        # {workspace, diff: [{text, kind: 'context'|'removed'|'added'}], ...}
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        # Set once the draft is (or would be) published, e.g. "148" for GTM-K2X9 v148.
        sa.Column("published_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'published', 'rejected')", name="ck_flux_draft_status"),
    )
    op.create_index("ix_flux_drafts_project_id", "flux_drafts", ["project_id"])
    op.create_index("ix_flux_drafts_conversation_id", "flux_drafts", ["conversation_id"])
    op.create_index("ix_flux_drafts_status", "flux_drafts", ["status"])
    op.create_index("idx_flux_draft_conv_status", "flux_drafts", ["conversation_id", "status"])


def downgrade() -> None:
    op.drop_table("flux_drafts")
