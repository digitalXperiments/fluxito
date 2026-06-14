"""059 — Append-only tp_activity change log.

Additive: one new table feeding the Activity drawer + review timeline. No
existing table, FK, or published-snapshot shape changes. Reversible.

Revision ID: 059_tp_activity
Revises: 058_property_list_and_bundles
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "061_tp_activity"
down_revision = "060_property_list_and_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tp_activity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tp_activity_entity", "tp_activity", ["plan_id", "branch_id", "entity_type", "entity_id"]
    )
    op.create_index("ix_tp_activity_feed", "tp_activity", ["plan_id", "branch_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tp_activity_feed", table_name="tp_activity")
    op.drop_index("ix_tp_activity_entity", table_name="tp_activity")
    op.drop_table("tp_activity")
