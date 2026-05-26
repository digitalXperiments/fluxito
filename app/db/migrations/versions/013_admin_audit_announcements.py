"""
Admin audit log, system announcements, and admin_role column on users.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "013_admin_audit_announcements"
down_revision = "012_templates_cross_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add admin_role column to users table ───────────────────────────────
    # Replaces the boolean is_super_admin with a proper role system.
    # Values: 'user' (default), 'admin', 'super_admin'
    op.add_column(
        "users",
        sa.Column(
            "admin_role",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )

    # Migrate existing is_super_admin=true rows to admin_role='super_admin'
    op.execute("""
        UPDATE users
        SET admin_role = 'super_admin'
        WHERE is_super_admin = true
    """)

    # Ensure the founding admin always has super_admin role
    op.execute("""
        UPDATE users
        SET admin_role = 'super_admin'
        WHERE email = 'ramnew2006@gmail.com'
    """)

    # ── Admin audit log ───────────────────────────────────────────────────
    op.create_table(
        "admin_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("admin_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("admin_email", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("target_email", sa.String(255), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_admin_audit_log_admin_id", "admin_audit_log", ["admin_id"])
    op.create_index("ix_admin_audit_log_action", "admin_audit_log", ["action"])
    op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])

    # ── System announcements ──────────────────────────────────────────────
    op.create_table(
        "system_announcements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default=sa.text("'info'")),
        sa.Column("target_tier", sa.String(16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_dismissible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_announcements")
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_action", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_admin_id", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_column("users", "admin_role")
