"""
012 — Templates & Cross-Platform Reporting

Creates the `templates` table for the template library (pre-built and user-created
dashboard recipes) and adds cross_platform_report to the PRO_ONLY_TOOLS gate.

Revision: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "012_templates_cross_platform"
down_revision = "011_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(32), nullable=False, server_default="custom", index=True),
        sa.Column("template_type", sa.String(16), nullable=False, server_default="user"),
        sa.Column("slug", sa.String(128), unique=True, nullable=False, index=True),
        sa.Column("icon", sa.String(32), nullable=True),
        sa.Column("required_platforms", JSONB, nullable=False, server_default="[]"),
        sa.Column("steps", JSONB, nullable=False, server_default="[]"),
        sa.Column("variables", JSONB, nullable=False, server_default="[]"),
        sa.Column("min_tier", sa.String(16), nullable=False, server_default="pro"),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_featured", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("templates")
