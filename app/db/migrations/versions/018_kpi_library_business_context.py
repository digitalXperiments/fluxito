"""KPI Library + Business Context

Adds two tenant-aware knowledge tables so that client-specific terminology
and business context can be injected into Claude answers via MCP tools.

Scoping rule (enforced in application code, not the schema):
  - Team plan users  → rows scoped by ``org_id``  (user_id is NULL)
  - Pro / Free users → rows scoped by ``user_id`` (org_id  is NULL)

Either ``user_id`` OR ``org_id`` must be set (CHECK constraint).

Revision ID: 018_kpi_library_business_context
Revises: 017_dashboard_insights
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "018_kpi_library_business_context"
down_revision = "017_dashboard_insights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── kpi_definitions ──
    op.create_table(
        "kpi_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Tenancy — exactly one of these is set
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # The KPI itself
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("data_source", sa.String(128), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
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
            "(user_id IS NOT NULL) <> (org_id IS NOT NULL)",
            name="ck_kpi_tenant_xor",
        ),
    )
    op.create_index("ix_kpi_definitions_user_id", "kpi_definitions", ["user_id"])
    op.create_index("ix_kpi_definitions_org_id", "kpi_definitions", ["org_id"])
    # Uniqueness of KPI name within a tenant (partial indexes — Postgres)
    op.execute(
        "CREATE UNIQUE INDEX uq_kpi_name_per_user "
        "ON kpi_definitions (user_id, lower(name)) WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_kpi_name_per_org "
        "ON kpi_definitions (org_id, lower(name)) WHERE org_id IS NOT NULL"
    )

    # ── business_contexts ──
    # One document per tenant (enforced by partial unique indexes below).
    op.create_table(
        "business_contexts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_by",
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
            "(user_id IS NOT NULL) <> (org_id IS NOT NULL)",
            name="ck_bizctx_tenant_xor",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_bizctx_user "
        "ON business_contexts (user_id) WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_bizctx_org "
        "ON business_contexts (org_id) WHERE org_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("business_contexts")
    op.drop_index("ix_kpi_definitions_org_id", table_name="kpi_definitions")
    op.drop_index("ix_kpi_definitions_user_id", table_name="kpi_definitions")
    op.drop_table("kpi_definitions")
