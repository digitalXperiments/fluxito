"""Tag Testing: automated test flows — audit_vendors, test_flows, test_flow_runs.

Also extends the audit_runs.audit_type CHECK constraint to include 'test_flow'
so a flow run can mirror an AuditRun into the auditing history.

Revision ID: 076_test_flows
Revises: 075_conversation_origin_section
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "076_test_flows"
down_revision = "075_conversation_origin_section"
branch_labels = None
depends_on = None


_AUDIT_TYPE_OLD = (
    "audit_type IN ('tag_audit','live_tag_test','data_quality','sdr_compliance',"
    "'platform_health','seo','warehouse','full_suite')"
)
_AUDIT_TYPE_NEW = (
    "audit_type IN ('tag_audit','live_tag_test','data_quality','sdr_compliance',"
    "'platform_health','seo','warehouse','full_suite','test_flow')"
)


def upgrade() -> None:
    # ── audit_vendors ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_vendors",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("url_pattern", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("params", JSONB(), nullable=False, server_default="[]"),
        sa.Column("catalog_slug", sa.String(128), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "slug", name="uq_audit_vendors_project_slug"),
    )
    op.create_index("ix_audit_vendors_project", "audit_vendors", ["project_id"])

    # ── test_flows ────────────────────────────────────────────────────────────
    op.create_table(
        "test_flows",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("device", sa.String(16), nullable=False, server_default="desktop"),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("steps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("schedule_cron", sa.String(128), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("notify", JSONB(), nullable=False, server_default="{}"),
        sa.Column("groups", JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_status", sa.String(16), nullable=False, server_default="never_run"),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "device IN ('desktop','mobile_web')",
            name="ck_test_flows_device",
        ),
        sa.CheckConstraint(
            "last_status IN ('passing','failing','error','never_run')",
            name="ck_test_flows_last_status",
        ),
    )
    op.create_index("ix_test_flows_project", "test_flows", ["project_id"])

    # ── test_flow_runs ────────────────────────────────────────────────────────
    op.create_table(
        "test_flow_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("flow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("assertions_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assertions_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_results", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("audit_run_id", UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["flow_id"], ["test_flows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["audit_runs.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('running','passing','failing','error')",
            name="ck_test_flow_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('manual','schedule')",
            name="ck_test_flow_runs_trigger",
        ),
    )
    op.create_index("ix_test_flow_runs_flow", "test_flow_runs", ["flow_id"])
    op.create_index("ix_test_flow_runs_project", "test_flow_runs", ["project_id"])

    # ── extend audit_runs.audit_type CHECK to include 'test_flow' ─────────────
    op.drop_constraint("ck_audit_runs_type", "audit_runs", type_="check")
    op.create_check_constraint("ck_audit_runs_type", "audit_runs", _AUDIT_TYPE_NEW)


def downgrade() -> None:
    op.drop_constraint("ck_audit_runs_type", "audit_runs", type_="check")
    op.create_check_constraint("ck_audit_runs_type", "audit_runs", _AUDIT_TYPE_OLD)

    op.drop_table("test_flow_runs")
    op.drop_table("test_flows")
    op.drop_table("audit_vendors")
