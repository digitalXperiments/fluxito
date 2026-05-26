"""036 — Revamp dashboard cards: drop artifact columns, add chart spec columns.

Transitions the dashboard system from an HTML-artifact approach (where Claude
generated full HTML documents stored in artifact_html / artifact_js) to a
card-native approach (where LLMs deploy structured card specs and the frontend
renders them directly).

Changes:
  dashboards  — drop artifact_js, artifact_meta, render_mode, artifact_html,
                artifact_html_improved, deployed_by, insights
  dashboard_cards — drop gcs_path; add chart_type (String) and chart_config (JSONB)

All existing dashboard data is truncated first because the schema change is
not backwards compatible and we are in active development with no production
data that needs preserving.

Revision ID: 036_revamp_dashboard_cards
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "036_revamp_dashboard_cards"
down_revision = "035_drop_ai_narrative"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Truncate all dashboard data. CASCADE is required: PostgreSQL won't truncate
    # a parent table while any FK constraint references it, even if the child is empty.
    op.execute("TRUNCATE TABLE dashboard_query_log, dashboard_cards, dashboards CASCADE")

    # Drop artifact columns from dashboards
    op.drop_column("dashboards", "artifact_js")
    op.drop_column("dashboards", "artifact_meta")
    op.drop_column("dashboards", "render_mode")
    op.drop_column("dashboards", "artifact_html")
    op.drop_column("dashboards", "artifact_html_improved")
    op.drop_column("dashboards", "deployed_by")
    op.drop_column("dashboards", "insights")

    # Drop GCS script column from dashboard_cards
    op.drop_column("dashboard_cards", "gcs_path")

    # Add explicit chart spec columns to dashboard_cards
    op.add_column("dashboard_cards", sa.Column("chart_type", sa.String(), nullable=True))
    op.add_column("dashboard_cards", sa.Column("chart_config", JSONB(), nullable=True))


def downgrade() -> None:
    # Remove new chart spec columns
    op.drop_column("dashboard_cards", "chart_config")
    op.drop_column("dashboard_cards", "chart_type")

    # Restore GCS script column
    op.add_column("dashboard_cards", sa.Column("gcs_path", sa.String(500), nullable=True))

    # Restore artifact columns on dashboards
    op.add_column("dashboards", sa.Column("insights", sa.Text(), nullable=True))
    op.add_column("dashboards", sa.Column("deployed_by", sa.String(100), nullable=True))
    op.add_column(
        "dashboards",
        sa.Column("artifact_html_improved", sa.Text(), nullable=True),
    )
    op.add_column("dashboards", sa.Column("artifact_html", sa.Text(), nullable=True))
    op.add_column(
        "dashboards",
        sa.Column("render_mode", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.add_column("dashboards", sa.Column("artifact_meta", JSONB(), nullable=True))
    op.add_column("dashboards", sa.Column("artifact_js", sa.Text(), nullable=True))
