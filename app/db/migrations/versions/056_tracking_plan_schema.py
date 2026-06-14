# app/db/migrations/versions/054_tracking_plan_schema.py
"""054 — Tracking Plan revamp: relational source-of-truth schema (tp_*)

Creates the 13 tp_* tables that replace the markdown-as-truth SDR model.
Branch-scoped content; published snapshots in tp_versions (JSONB).

Revision ID: 054_tracking_plan_schema
Revises: 053_dashboard_filter_presets
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "056_tracking_plan_schema"
down_revision = "055_dashboard_cache_ttl"
branch_labels = None
depends_on = None


def _id_col():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"))


def upgrade() -> None:
    # tp_plans (FKs to tp_branches / tp_versions added later — circular)
    op.create_table(
        "tp_plans",
        _id_col(),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_branch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("current_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("intake_answers", JSONB(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_tp_plan_per_project"),
    )

    op.create_table(
        "tp_branches",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # ondelete="SET NULL" — matches TPBranch.base_branch_id in the model
        sa.Column(
            "base_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("base_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        _ts("created_at"),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("plan_id", "name", name="uq_tp_branch_name"),
        sa.CheckConstraint("status IN ('active', 'merged', 'abandoned')", name="ck_tp_branch_status"),
    )

    op.create_table(
        "tp_versions",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ondelete="CASCADE" — matches TPVersion.branch_id in the model
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        _ts("published_at"),
        sa.UniqueConstraint("plan_id", "version_number", name="uq_tp_version"),
    )

    op.create_foreign_key(
        "fk_tp_plan_default_branch", "tp_plans", "tp_branches", ["default_branch_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_tp_plan_current_version", "tp_plans", "tp_versions", ["current_version_id"], ["id"]
    )

    op.create_table(
        "tp_categories",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_category_name"),
    )

    op.create_table(
        "tp_events",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tags", ARRAY(sa.Text()), nullable=True),
        sa.Column("trigger_type", sa.Text(), nullable=True),
        sa.Column("trigger_config", JSONB(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("owner_business", sa.Text(), nullable=True),
        sa.Column("owner_technical", sa.Text(), nullable=True),
        sa.Column("consent_required", ARRAY(sa.Text()), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_event_name"),
    )

    op.create_table(
        "tp_properties",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="event"),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("constraints", JSONB(), nullable=True),
        sa.Column(
            "parent_property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_pii", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "kind", "name", name="uq_tp_property_name"),
        sa.CheckConstraint("kind IN ('event', 'user', 'group', 'system')", name="ck_tp_property_kind"),
        sa.CheckConstraint(
            "data_type IN ('string', 'int', 'float', 'boolean', 'object', 'array')",
            name="ck_tp_property_data_type",
        ),
    )

    op.create_table(
        "tp_event_properties",
        _id_col(),
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("override_description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("event_id", "property_id", name="uq_tp_event_property"),
    )

    op.create_table(
        "tp_sources",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("platform_type", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("connector_ref", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_source_name"),
    )

    op.create_table(
        "tp_destinations",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("platform_account_id", sa.Text(), nullable=True),
        sa.Column("config", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_destination_name"),
    )

    op.create_table(
        "tp_source_destinations",
        _id_col(),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_destinations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", "destination_id", name="uq_tp_source_destination"),
    )

    op.create_table(
        "tp_event_sources",
        _id_col(),
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("implementation_status", sa.Text(), nullable=False, server_default="planned"),
        sa.UniqueConstraint("event_id", "source_id", name="uq_tp_event_source"),
        sa.CheckConstraint(
            "implementation_status IN ('planned', 'implemented', 'verified', 'deprecated')",
            name="ck_tp_event_source_status",
        ),
    )

    op.create_table(
        "tp_event_destinations",
        _id_col(),
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_destinations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dest_event_name", sa.Text(), nullable=True),
        sa.Column("property_mappings", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", "destination_id", name="uq_tp_event_destination"),
    )

    op.create_table(
        "tp_metrics",
        _id_col(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False, server_default="count"),
        # ondelete="SET NULL" — matches TPMetric.event_id in the model
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filters", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_metric_name"),
        sa.CheckConstraint(
            "type IN ('count', 'sum', 'unique', 'average', 'ratio')", name="ck_tp_metric_type"
        ),
    )

    # Helpful indexes for branch-scoped reads
    op.create_index("ix_tp_events_branch", "tp_events", ["branch_id"])
    op.create_index("ix_tp_properties_branch", "tp_properties", ["branch_id"])
    op.create_index("ix_tp_event_properties_event", "tp_event_properties", ["event_id"])
    op.create_index("ix_tp_event_sources_event", "tp_event_sources", ["event_id"])
    op.create_index("ix_tp_event_destinations_event", "tp_event_destinations", ["event_id"])


def downgrade() -> None:
    op.drop_constraint("fk_tp_plan_current_version", "tp_plans", type_="foreignkey")
    op.drop_constraint("fk_tp_plan_default_branch", "tp_plans", type_="foreignkey")
    for table in (
        "tp_metrics",
        "tp_event_destinations",
        "tp_event_sources",
        "tp_source_destinations",
        "tp_destinations",
        "tp_sources",
        "tp_event_properties",
        "tp_properties",
        "tp_events",
        "tp_categories",
        "tp_versions",
        "tp_branches",
        "tp_plans",
    ):
        op.drop_table(table)
