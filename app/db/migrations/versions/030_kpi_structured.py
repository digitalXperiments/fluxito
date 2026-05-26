"""030 — Structured KPI library.

Replaces the freeform ``kpi_definitions`` glossary with a structured
``kpis`` catalog + per-input bindings (``kpi_inputs``). The new schema
treats each KPI as an executable spec rather than a text entry, so the
MCP-connected AI has grounded context (typed formulas, connector
bindings, units, direction, thresholds) instead of hand-typed strings.

This is a hard cutover — the project is still in dev and there is no
production KPI data to preserve, so the old table is dropped rather
than migrated in place.

Shape at a glance:

    kpis(
        id, project_id, slug, name, aliases, status, version,
        description, business_question, interpretation_guide,
        expression, time_grain, unit, format_spec, direction,
        target_value, target_type, expected_range_min/max,
        category, tags, owner, source_of_truth_url,
        last_reviewed_at, reviewed_by,
        created_by, created_at, updated_at
    )

    kpi_inputs(
        id, kpi_id, key, connection_id, binding (jsonb),
        created_at
    )

Revision ID: 030_kpi_structured
Revises: 029_optimization_indexes
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "030_kpi_structured"
down_revision = "029_optimization_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the freeform glossary table — no data to preserve (dev-only).
    op.execute("DROP INDEX IF EXISTS uq_kpi_name_per_user")
    op.execute("DROP INDEX IF EXISTS uq_kpi_name_per_org")
    op.execute("DROP INDEX IF EXISTS ix_kpi_definitions_user_id")
    op.execute("DROP INDEX IF EXISTS ix_kpi_definitions_org_id")
    op.execute("DROP INDEX IF EXISTS ix_kpi_definitions_project_id")
    op.execute("DROP TABLE IF EXISTS kpi_definitions")

    # ── kpis ──
    op.create_table(
        "kpis",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Identity
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("aliases", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Lifecycle
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Definition
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("business_question", sa.Text(), nullable=True),
        sa.Column("interpretation_guide", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Computation spec — expression references input keys, e.g. "{a} / {b}".
        # Null until the structured picker is filled in (Phase 3).
        sa.Column("expression", sa.Text(), nullable=True),
        sa.Column("time_grain", sa.String(32), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("format_spec", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        # Quality
        sa.Column("target_value", sa.Numeric(), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("expected_range_min", sa.Numeric(), nullable=True),
        sa.Column("expected_range_max", sa.Numeric(), nullable=True),
        # Ownership
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("source_of_truth_url", sa.String(512), nullable=True),
        # Audit
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft','approved','deprecated')",
            name="ck_kpis_status",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('higher_better','lower_better','neutral')",
            name="ck_kpis_direction",
        ),
    )
    # Slug is stable per project and case-insensitive unique.
    op.execute(
        "CREATE UNIQUE INDEX uq_kpis_project_slug "
        "ON kpis (project_id, lower(slug))"
    )
    # Name uniqueness within a project — guards against duplicate entries
    # even before a slug is curated.
    op.execute(
        "CREATE UNIQUE INDEX uq_kpis_project_name "
        "ON kpis (project_id, lower(name))"
    )
    op.create_index("ix_kpis_status", "kpis", ["status"])

    # ── kpi_inputs ──
    op.create_table(
        "kpi_inputs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "kpi_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kpis.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Reference key used inside ``kpis.expression`` (e.g. "a", "spend").
        sa.Column("key", sa.String(32), nullable=False),
        # Source platform — dictates which connector + which connection table
        # (OAuthConnection for google/meta/etc., BQConnection for bigquery,
        # credential models for amplitude/adobe). The FK-less ``connection_id``
        # below is resolved against the right table using ``source``.
        sa.Column("source", sa.String(32), nullable=False),
        # No FK constraint — connection_id may point at different tables
        # depending on ``source``. Integrity is enforced in application code.
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        # Connector-specific shape validated in application code:
        #   GA4:       {property_id, metric|dimension, aggregation?, filters?}
        #   BigQuery:  {project, dataset, table, field, aggregation, filters?}
        sa.Column("binding", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_kpi_inputs_kpi_key "
        "ON kpi_inputs (kpi_id, key)"
    )


def downgrade() -> None:
    op.drop_table("kpi_inputs")
    op.execute("DROP INDEX IF EXISTS uq_kpis_project_slug")
    op.execute("DROP INDEX IF EXISTS uq_kpis_project_name")
    op.drop_index("ix_kpis_status", table_name="kpis")
    op.drop_table("kpis")

    # Recreate a minimal kpi_definitions so downgrading doesn't leave the
    # app broken if the downstream code is rolled back alongside.
    op.create_table(
        "kpi_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("data_source", sa.String(128), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
