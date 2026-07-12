"""074 — tp_drift: tracking-plan live-vs-plan drift reconciliation.

Backs the TP Event Detail redesign (Ledger revamp Phase 2). Three tables hold
OBSERVED reality computed from GA4 (event volume, firing) and BigQuery
(parameter fill-rate, unplanned params), read back by the tracking-plan
serializer to drive the drift badges, health strip, and parameter table:

  tp_event_drift        — one row per (plan, event name); status + volume + coverage.
  tp_param_observation  — one row per (plan, event name, param key); presence % + unplanned flag.
  tp_drift_config       — per-project wiring: which GA4 property + BQ export dataset to observe.

Keyed by event NAME (not tp_events.id) because live analytics data speaks names:
a name may be unplanned (no matching plan event) or a plan event may be broken
(never firing). Drift rows are cache, rebuilt on each run.

Additive: three new tables. Reversible.

Revision ID: 074_tp_drift
Revises: 073_briefing_dismissals
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "074_tp_drift"
down_revision = "073_briefing_dismissals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tp_event_drift",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="in_plan"),
        sa.Column("volume_7d", sa.Integer(), nullable=True),
        sa.Column("param_coverage_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_id", "event_name", name="uq_tp_event_drift"),
        sa.CheckConstraint(
            "status IN ('verified', 'in_plan', 'drifted', 'broken', 'unplanned')",
            name="ck_tp_event_drift_status",
        ),
    )
    op.create_index("ix_tp_event_drift_plan", "tp_event_drift", ["plan_id"])

    op.create_table(
        "tp_param_observation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("param_key", sa.Text(), nullable=False),
        sa.Column("present_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("sample_value", sa.Text(), nullable=True),
        sa.Column("data_type_observed", sa.Text(), nullable=True),
        sa.Column("is_unplanned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_id", "event_name", "param_key", name="uq_tp_param_observation"),
    )
    op.create_index("ix_tp_param_observation_event", "tp_param_observation", ["plan_id", "event_name"])

    op.create_table(
        "tp_drift_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ga4_property_id", sa.Text(), nullable=True),
        sa.Column(
            "bq_connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bq_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bq_dataset", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_tp_drift_config_project"),
    )
    op.create_index("ix_tp_drift_config_project", "tp_drift_config", ["project_id"])


def downgrade() -> None:
    op.drop_table("tp_drift_config")
    op.drop_table("tp_param_observation")
    op.drop_table("tp_event_drift")
