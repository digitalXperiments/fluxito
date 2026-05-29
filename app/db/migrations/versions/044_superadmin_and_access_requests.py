"""Add is_superadmin to users, backfill earliest user, create access_requests.

Revision ID: 044_superadmin_and_access_requests
Revises: 043_sdr_source_xlsx
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "044_superadmin_and_access_requests"
down_revision = "043_sdr_source_xlsx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE users SET is_superadmin = true "
        "WHERE id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1)"
    )
    op.create_table(
        "access_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("use_case", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_access_requests_email", "access_requests", ["email"])


def downgrade() -> None:
    op.drop_index("ix_access_requests_email", table_name="access_requests")
    op.drop_table("access_requests")
    op.drop_column("users", "is_superadmin")
