"""
Tool Call Audit Trail

Creates the `tool_call_audit` table that stores every MCP tool invocation with
the exact arguments, a truncated response, status, duration, and source client.
This powers the user-facing "Answer audit trail" page and lets users click
into any AI answer to see where the number came from.

Revision ID: 019_tool_call_audit
Revises: 018_kpi_library_business_context
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "019_tool_call_audit"
down_revision = "018_kpi_library_business_context"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_call_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column("source_client", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("is_write", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("arguments", JSONB, nullable=True),
        # Small, human-readable one-liner derived from the response (e.g.
        # "12 rows · 4 columns" or "Campaign list: 8 items"). Shown in the
        # audit list view.
        sa.Column("response_summary", sa.String(512), nullable=True),
        # Full tool response, truncated to ~32KB to keep the table lean.
        sa.Column("response_preview", sa.Text(), nullable=True),
        sa.Column("response_truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tool_call_audit_user_created",
        "tool_call_audit",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_tool_call_audit_tool_name",
        "tool_call_audit",
        ["tool_name"],
    )


def downgrade():
    op.drop_index("ix_tool_call_audit_tool_name", table_name="tool_call_audit")
    op.drop_index("ix_tool_call_audit_user_created", table_name="tool_call_audit")
    op.drop_table("tool_call_audit")
