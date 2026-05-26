"""Drop billing tables and columns for OSS release.

Drops:
  Tables: usage_ledger, admin_audit_log, system_announcements
  Columns on projects: plan, stripe_customer_id, stripe_subscription_id,
                       billing_cycle, max_seats, queries_included,
                       current_period_reset, trial_ends_at
  Constraint on projects: ck_project_plan_valid
  Column on users: admin_role

This migration is destructive and not safely reversible. Downgrade
recreates the schema (with simplified types) but does not restore data.

Revision ID: 037_drop_billing
Revises: 036_revamp_dashboard_cards
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "037_drop_billing"
down_revision = "036_revamp_dashboard_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop billing-only tables — drop these first since they FK into users/projects
    op.drop_table("usage_ledger")
    op.drop_table("admin_audit_log")
    op.drop_table("system_announcements")

    # 2. Drop check constraint on projects.plan before dropping the column
    op.drop_constraint("ck_project_plan_valid", "projects", type_="check")

    # 3. Drop billing columns on projects (alphabetical for cleanliness)
    op.drop_column("projects", "billing_cycle")
    op.drop_column("projects", "current_period_reset")
    op.drop_column("projects", "max_seats")
    op.drop_column("projects", "plan")
    op.drop_column("projects", "queries_included")
    op.drop_column("projects", "stripe_customer_id")
    op.drop_column("projects", "stripe_subscription_id")
    op.drop_column("projects", "trial_ends_at")

    # 4. Drop platform-admin role on users
    op.drop_column("users", "admin_role")


def downgrade() -> None:
    # Restore admin_role on users
    op.add_column(
        "users",
        sa.Column("admin_role", sa.String(16), nullable=False, server_default="user"),
    )

    # Restore billing columns on projects
    op.add_column("projects", sa.Column("plan", sa.String(16), nullable=False, server_default="free"))
    op.add_column("projects", sa.Column("stripe_customer_id", sa.String(255), nullable=True))
    op.add_column("projects", sa.Column("stripe_subscription_id", sa.String(255), nullable=True))
    op.add_column("projects", sa.Column("billing_cycle", sa.String(16), nullable=True))
    op.add_column("projects", sa.Column("max_seats", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("projects", sa.Column("queries_included", sa.Integer(), nullable=False, server_default="500"))
    op.add_column("projects", sa.Column("current_period_reset", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("trial_ends_at", sa.DateTime(), nullable=True))

    op.create_check_constraint(
        "ck_project_plan_valid",
        "projects",
        "plan IN ('free', 'pro', 'team')",
    )

    # Recreate billing tables (schema only — data is gone)
    # usage_ledger: UUIDs for id and FK columns, as per original schema
    op.create_table(
        "usage_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("month_key", sa.String(7), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    # admin_audit_log: UUID id, admin_id FK with SET NULL
    op.create_table(
        "admin_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("admin_email", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("target_email", sa.String(255), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="SET NULL"),
    )
    # system_announcements: UUID id, created_by FK with SET NULL
    op.create_table(
        "system_announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("target_tier", sa.String(16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_dismissible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
