"""057 — Tracking-plan comments (threads + @-mentions, branch-scoped).

Creates ``tp_comments``:
- threaded replies via parent_id self-FK
- comments attach to any branch-local entity (event, property, source,
  destination, metric, category) or to the branch/plan itself
- @-mentions stored as a UUID[] array
- soft-resolve flag (resolved=true hides the thread from default views)

Revision ID: 057_tp_comments
Revises: 056_branch_review_fields
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "057_tp_comments"
down_revision = "056_branch_review_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tp_comments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions", ARRAY(UUID(as_uuid=True)), nullable=True),
        sa.Column(
            "resolved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "entity_type IN ('event','property','source','destination','metric','category','plan','branch')",
            name="ck_tp_comment_entity_type",
        ),
    )
    op.create_index(
        "ix_tp_comments_branch_entity",
        "tp_comments",
        ["branch_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tp_comments_branch_entity", table_name="tp_comments")
    op.drop_table("tp_comments")
