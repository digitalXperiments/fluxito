"""Add insights column to dashboards.

Stores an AI-generated narrative summary of the dashboard's findings.
Populated by the MCP tool `dashboard_deploy` on every call, and editable
via `dashboard_edit`.

Revision ID: 017_dashboard_insights
Revises: 016_activity_log
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa


revision = "017_dashboard_insights"
down_revision = "016_activity_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column("insights", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dashboards", "insights")
