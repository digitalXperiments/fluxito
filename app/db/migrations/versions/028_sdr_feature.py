"""028 — Solution Design Reference (SDR) feature

A *Solution Design Reference* is the canonical document that answers:
"What events should fire, when, with what parameters, to which destinations,
and why?"

One SDR per project (MVP constraint). Markdown is the source of truth;
structured projections (sdr_events, sdr_parameters, sdr_destinations) are
rebuilt on every save for fast queries by audit tools.

Tables created:
  ``sdrs``                  — Main SDR record, one per project.
  ``sdr_versions``          — Immutable approved-version snapshots.
  ``sdr_events``            — Projection: events parsed from markdown.
  ``sdr_parameters``        — Projection: parameters per event.
  ``sdr_destinations``      — Projection: per-event destination mappings.
  ``sdr_refinement_state``  — Resumable refinement conversation state.

Revision ID: 028_sdr_feature
Revises: 027_drop_legacy_user_flags
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


revision = "028_sdr_feature"
down_revision = "027_drop_legacy_user_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # sdrs — main SDR record, one per project
    # ------------------------------------------------------------------
    op.create_table(
        "sdrs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("current_version_id", UUID(as_uuid=True), nullable=True),  # FK added after sdr_versions
        sa.Column("markdown_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'approved', 'archived')", name="ck_sdr_status"),
        sa.UniqueConstraint("project_id", name="uq_sdr_per_project"),
    )

    # ------------------------------------------------------------------
    # sdr_versions — immutable approved snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "sdr_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sdr_id", UUID(as_uuid=True), sa.ForeignKey("sdrs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Text(), nullable=False),
        sa.Column("markdown_snapshot", sa.Text(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("sdr_id", "version_number", name="uq_sdr_version"),
    )

    # Now add FK from sdrs.current_version_id -> sdr_versions.id
    op.create_foreign_key(
        "fk_sdr_current_version",
        "sdrs",
        "sdr_versions",
        ["current_version_id"],
        ["id"],
    )

    # ------------------------------------------------------------------
    # sdr_events — structured projection (rebuilt on every save)
    # ------------------------------------------------------------------
    op.create_table(
        "sdr_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sdr_id", UUID(as_uuid=True), sa.ForeignKey("sdrs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("trigger_type", sa.Text(), nullable=True),
        sa.Column("trigger_config", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=True,
        ),
        sa.Column("owner_business", sa.Text(), nullable=True),
        sa.Column("owner_technical", sa.Text(), nullable=True),
        sa.Column("consent_required", ARRAY(sa.Text()), nullable=True),
        sa.Column("kpi_links", ARRAY(sa.Text()), nullable=True),
        sa.UniqueConstraint("sdr_id", "name", name="uq_sdr_event_name"),
        sa.CheckConstraint(
            "status IS NULL OR status IN ('planned', 'implemented', 'verified', 'deprecated')",
            name="ck_sdr_event_status",
        ),
    )
    op.create_index("idx_sdr_events_sdr_name", "sdr_events", ["sdr_id", "name"])
    op.create_index("idx_sdr_events_status", "sdr_events", ["sdr_id", "status"])

    # ------------------------------------------------------------------
    # sdr_parameters — per-event parameter projection
    # ------------------------------------------------------------------
    op.create_table(
        "sdr_parameters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("sdr_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("validation_rule", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", "name", name="uq_sdr_param_name"),
    )
    op.create_index("idx_sdr_parameters_event", "sdr_parameters", ["event_id"])

    # ------------------------------------------------------------------
    # sdr_destinations — per-event destination mapping projection
    # ------------------------------------------------------------------
    op.create_table(
        "sdr_destinations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("sdr_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("platform_account_id", sa.Text(), nullable=True),
        sa.Column("dest_event_name", sa.Text(), nullable=True),
        sa.Column("mapping", JSONB, nullable=True),
    )
    op.create_index("idx_sdr_destinations_event", "sdr_destinations", ["event_id"])

    # ------------------------------------------------------------------
    # sdr_refinement_state — resumable conversation state
    # ------------------------------------------------------------------
    op.create_table(
        "sdr_refinement_state",
        sa.Column("sdr_id", UUID(as_uuid=True), sa.ForeignKey("sdrs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("current_section", sa.Text(), nullable=False),
        sa.Column("sections_completed", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pending_proposed_changes", JSONB, nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("sdr_refinement_state")
    op.drop_table("sdr_destinations")
    op.drop_table("sdr_parameters")
    op.drop_table("sdr_events")
    op.drop_constraint("fk_sdr_current_version", "sdrs", type_="foreignkey")
    op.drop_table("sdr_versions")
    op.drop_table("sdrs")
