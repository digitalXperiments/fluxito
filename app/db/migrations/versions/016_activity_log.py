"""
Add activity log columns to usage_ledger + create activity_events table.

Enriches the existing usage_ledger with status, is_write, and source_client
columns for the user-facing activity log. Creates a new activity_events table
for non-tool-call events (sign-ins, connections, permission changes).

Revision ID: 016_activity_log
Revises: 015_tutorial_completed
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "016_activity_log"
down_revision = "015_tutorial_completed"
branch_labels = None
depends_on = None


def upgrade():
    # Enrich usage_ledger with activity log columns
    op.add_column(
        "usage_ledger",
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
    )
    op.add_column(
        "usage_ledger",
        sa.Column("is_write", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "usage_ledger",
        sa.Column("source_client", sa.String(64), nullable=True),
    )

    # Create activity_events for auth/connection events
    op.create_table(
        "activity_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("activity_events")
    op.drop_column("usage_ledger", "source_client")
    op.drop_column("usage_ledger", "is_write")
    op.drop_column("usage_ledger", "status")
