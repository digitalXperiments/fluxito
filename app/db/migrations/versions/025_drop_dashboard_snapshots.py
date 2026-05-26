"""025 — Drop dashboard_snapshots.

The snapshot feature (frozen point-in-time captures under /s/{slug}) is
removed in favour of two better-scoped primitives:

  * On-demand "Share PDF"  — the /dashboards/{slug}/pdf endpoint renders a
    PDF from live data with no intermediate persistence. Users who want to
    hand someone a specific moment can email/Slack the PDF.
  * Scheduled reports      — report_schedules + report_runs (migration 024)
    send a fresh PDF to email/Slack on a cadence; no payload is persisted.

Neither of those needs the dashboard_snapshots table, so this migration
drops the table and its indexes. The model, routes, templates, and UI
entry points are removed in the same PR (Step 6).

Revision ID: 025_drop_dashboard_snapshots
Revises: 024_scheduled_reports
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "025_drop_dashboard_snapshots"
down_revision = "024_scheduled_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Indexes first — Postgres drops them with the table, but being explicit
    # keeps the downgrade path symmetric and avoids "index does not exist"
    # warnings if the table is dropped out from under us on a partial re-run.
    op.execute("DROP INDEX IF EXISTS ix_dashboard_snapshots_dashboard_id")
    op.execute("DROP INDEX IF EXISTS ix_dashboard_snapshots_user_id")
    op.execute("DROP INDEX IF EXISTS ix_dashboard_snapshots_slug")
    op.execute("DROP TABLE IF EXISTS dashboard_snapshots")


def downgrade() -> None:
    # Mirror of migration 008_dashboard_snapshots.upgrade() — rebuilds the
    # empty table so an operator can roll back without a full DB restore.
    # Data is NOT recoverable; this only restores the schema shape.
    op.create_table(
        "dashboard_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dashboard_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_email", sa.String(255), nullable=False, server_default=""),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("slug", sa.String(24), nullable=False, unique=True),
        sa.Column("snapshot_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("filter_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
        sa.Column("password_hash", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_dashboard_snapshots_slug",
        "dashboard_snapshots",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "ix_dashboard_snapshots_user_id",
        "dashboard_snapshots",
        ["user_id"],
    )
    op.create_index(
        "ix_dashboard_snapshots_dashboard_id",
        "dashboard_snapshots",
        ["dashboard_id"],
    )
