"""Auditing Platform: add audit_runs, audit_findings, tag_custom_rules, ltt_test_plans tables.

Revision ID: 052_auditing_platform
Revises: 051_mcp_pat_support
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "052_auditing_platform"
down_revision = "051_mcp_pat_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_runs",
        sa.Column("id",            UUID(as_uuid=True),   nullable=False,
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id",    UUID(as_uuid=True),   nullable=False),
        sa.Column("audit_type",    sa.String(32),         nullable=False),
        sa.Column("title",         sa.String(255),        nullable=True),
        sa.Column("score",         sa.Integer(),          nullable=True),
        sa.Column("critical_count",sa.Integer(),          nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(),          nullable=False, server_default="0"),
        sa.Column("info_count",    sa.Integer(),          nullable=False, server_default="0"),
        sa.Column("passed_count",  sa.Integer(),          nullable=False, server_default="0"),
        sa.Column("status",        sa.String(16),         nullable=False, server_default="complete"),
        sa.Column("triggered_by",  sa.String(16),         nullable=False, server_default="claude"),
        sa.Column("url_tested",    sa.Text(),             nullable=True),
        sa.Column("ltt_session_id",sa.String(64),         nullable=True),
        sa.Column("raw_summary",   sa.Text(),             nullable=True),
        sa.Column("sdr_version_id",UUID(as_uuid=True),   nullable=True),
        sa.Column("created_by",    UUID(as_uuid=True),   nullable=False),
        sa.Column("created_at",    sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("duration_ms",   sa.Integer(),          nullable=True),
        sa.CheckConstraint(
            "audit_type IN ('tag_audit','live_tag_test','data_quality','sdr_compliance',"
            "'platform_health','seo','warehouse','full_suite')",
            name="ck_audit_runs_type",
        ),
        sa.CheckConstraint(
            "status IN ('running','complete','error')",
            name="ck_audit_runs_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('claude','schedule','manual')",
            name="ck_audit_runs_triggered_by",
        ),
    )
    op.create_index("ix_audit_runs_project_created",
                    "audit_runs", ["project_id", sa.text("created_at DESC")])
    op.create_index("ix_audit_runs_project_type_created",
                    "audit_runs", ["project_id", "audit_type", sa.text("created_at DESC")])

    # ── audit_findings ────────────────────────────────────────────────────────
    op.create_table(
        "audit_findings",
        sa.Column("id",           UUID(as_uuid=True),  nullable=False,
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("run_id",       UUID(as_uuid=True),  nullable=False),
        sa.Column("project_id",   UUID(as_uuid=True),  nullable=False),
        sa.Column("domain",       sa.String(32),        nullable=True),
        sa.Column("platform",     sa.String(64),        nullable=True),
        sa.Column("severity",     sa.String(16),        nullable=True),
        sa.Column("rule_id",      sa.String(128),       nullable=True),
        sa.Column("event",        sa.String(128),       nullable=True),
        sa.Column("entity_type",  sa.String(32),        nullable=True),
        sa.Column("entity_id",    sa.Text(),            nullable=True),
        sa.Column("entity_label", sa.Text(),            nullable=True),
        sa.Column("passed",       sa.Boolean(),         nullable=False, server_default="false"),
        sa.Column("expected",     JSONB(),              nullable=True),
        sa.Column("actual",       JSONB(),              nullable=True),
        sa.Column("message",      sa.Text(),            nullable=True),
        sa.Column("remediation",  sa.Text(),            nullable=True),
        sa.Column("source",       sa.String(32),        nullable=True, server_default="rule_book"),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_audit_findings_run_severity",   "audit_findings", ["run_id", "severity"])
    op.create_index("ix_audit_findings_run_platform",   "audit_findings", ["run_id", "platform"])
    op.create_index("ix_audit_findings_project_platform_created",
                    "audit_findings", ["project_id", "platform", sa.text("created_at DESC")])

    # ── tag_custom_rules ──────────────────────────────────────────────────────
    op.create_table(
        "tag_custom_rules",
        sa.Column("id",               UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id",       UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id",          sa.String(128),      nullable=False),
        sa.Column("platform",         sa.String(64),       nullable=False, server_default="*"),
        sa.Column("event",            sa.String(128),      nullable=False, server_default="*"),
        sa.Column("name",             sa.String(255),      nullable=False),
        sa.Column("description",      sa.Text(),           nullable=True),
        sa.Column("required_params",  JSONB(),             nullable=False, server_default="[]"),
        sa.Column("forbidden_params", JSONB(),             nullable=False, server_default="[]"),
        sa.Column("param_assertions", JSONB(),             nullable=False, server_default="[]"),
        sa.Column("severity",         sa.String(16),       nullable=False, server_default="warning"),
        sa.Column("remediation",      sa.Text(),           nullable=True),
        sa.Column("is_active",        sa.Boolean(),        nullable=False, server_default="true"),
        sa.Column("created_by",       UUID(as_uuid=True),  nullable=False),
        sa.Column("created_at",       sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",       sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "rule_id", name="uq_tag_custom_rules_project_rule"),
        sa.CheckConstraint(
            "severity IN ('critical','warning','info')",
            name="ck_tag_custom_rules_severity",
        ),
    )
    op.create_index("ix_tag_custom_rules_project", "tag_custom_rules", ["project_id"])

    # ── ltt_test_plans ────────────────────────────────────────────────────────
    op.create_table(
        "ltt_test_plans",
        sa.Column("id",                UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id",        UUID(as_uuid=True), nullable=False),
        sa.Column("name",              sa.String(255),      nullable=False),
        sa.Column("url_patterns",      JSONB(),             nullable=True),
        sa.Column("interaction_steps", JSONB(),             nullable=True),
        sa.Column("expected_platforms",JSONB(),             nullable=True),
        sa.Column("created_by",        UUID(as_uuid=True),  nullable=True),
        sa.Column("created_at",        sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ltt_test_plans_project", "ltt_test_plans", ["project_id"])


def downgrade() -> None:
    op.drop_table("ltt_test_plans")
    op.drop_table("tag_custom_rules")
    op.drop_table("audit_findings")
    op.drop_table("audit_runs")
