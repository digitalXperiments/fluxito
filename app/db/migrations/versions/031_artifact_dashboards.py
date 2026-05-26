"""031 — Artifact dashboard rendering system.

Adds columns to support Claude-authored JS module rendering:
  dashboards: artifact_js, artifact_meta, render_mode
  projects: dashboard_style_config, ai_narrative_endpoint, ai_narrative_key_enc

Revision ID: 031_artifact_dashboards
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "031_artifact_dashboards"
down_revision = "030_kpi_structured"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("artifact_js", sa.Text(), nullable=True))
    op.add_column(
        "dashboards",
        sa.Column("artifact_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "dashboards",
        sa.Column("render_mode", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "dashboard_style_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("projects", sa.Column("ai_narrative_endpoint", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("ai_narrative_key_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "render_mode")
    op.drop_column("dashboards", "artifact_meta")
    op.drop_column("dashboards", "artifact_js")
    op.drop_column("projects", "ai_narrative_key_enc")
    op.drop_column("projects", "ai_narrative_endpoint")
    op.drop_column("projects", "dashboard_style_config")
