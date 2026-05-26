"""
Consolidate usage_ledger into a per-(user, month) rollup table.

Historically, `usage_ledger` had one row per billable tool call. This was
duplicated by the new `tool_call_audit` table (migration 019), which stores
the same per-call metadata plus full arguments and response payloads.

After this migration:
  * `tool_call_audit` is the single source of truth for per-call data
    (used by the unified activity/audit page, admin tool breakdowns, and
    user-facing usage stats).
  * `usage_ledger` becomes a lightweight monthly counter: one row per
    (user_id, month_key) with a `count` column. Used only by the billing
    quota check, which now UPSERTs on insert.

Per-call columns (tool_name, platform, billed_at, status, is_write,
source_client) are dropped. Historical per-call detail remains available
for the audit window via `tool_call_audit`.

Revision ID: 020_usage_ledger_rollup
Revises: 019_tool_call_audit
"""

from alembic import op
import sqlalchemy as sa

revision = "020_usage_ledger_rollup"
down_revision = "019_tool_call_audit"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Add the new rollup `count` column (defaulted to 1 so the pre-roll
    #    aggregate math below works on existing per-call rows).
    op.add_column(
        "usage_ledger",
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
    )
    # An `updated_at` stamp that reflects the last time the counter ticked.
    op.add_column(
        "usage_ledger",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # 2) Roll per-call rows up to one row per (user_id, month_key).
    #    We build a rollup CTE, truncate the ledger, and reinsert one row
    #    per bucket. Safer than an UPDATE-delete dance because the old
    #    table had no unique constraint on (user_id, month_key).
    op.execute(
        """
        CREATE TEMP TABLE _usage_rollup AS
        SELECT
            gen_random_uuid() AS id,
            user_id,
            month_key,
            COUNT(*)::int     AS count,
            MAX(billed_at)    AS updated_at
        FROM usage_ledger
        GROUP BY user_id, month_key;
        """
    )
    op.execute("DELETE FROM usage_ledger;")
    op.execute(
        """
        INSERT INTO usage_ledger (id, user_id, month_key, count, updated_at, billed_at, tool_name, status, is_write)
        SELECT id, user_id, month_key, count, updated_at, updated_at, 'rollup', 'success', false
        FROM _usage_rollup;
        """
    )
    op.execute("DROP TABLE _usage_rollup;")

    # 3) Drop per-call columns that no longer apply at the rollup level.
    op.drop_column("usage_ledger", "platform")
    op.drop_column("usage_ledger", "source_client")
    op.drop_column("usage_ledger", "is_write")
    op.drop_column("usage_ledger", "status")
    op.drop_column("usage_ledger", "tool_name")
    op.drop_column("usage_ledger", "billed_at")

    # 4) Enforce one row per (user, month) so the upsert in quota.py can
    #    rely on a real UNIQUE constraint via ON CONFLICT.
    op.create_unique_constraint(
        "uq_usage_ledger_user_month",
        "usage_ledger",
        ["user_id", "month_key"],
    )


def downgrade():
    # Partial downgrade: re-add the per-call columns so older code paths
    # that still SELECT them don't break, but we CANNOT reconstruct the
    # original per-call rows from the rollup.
    op.drop_constraint("uq_usage_ledger_user_month", "usage_ledger", type_="unique")
    op.add_column(
        "usage_ledger",
        sa.Column("tool_name", sa.String(64), nullable=True),
    )
    op.add_column(
        "usage_ledger",
        sa.Column("platform", sa.String(32), nullable=True),
    )
    op.add_column(
        "usage_ledger",
        sa.Column(
            "billed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
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
    op.drop_column("usage_ledger", "updated_at")
    op.drop_column("usage_ledger", "count")
