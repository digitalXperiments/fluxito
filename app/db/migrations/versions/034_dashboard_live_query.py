"""034 — Dashboard live query columns and audit log table.

query_scopes: JSONB list of {platform, property_id?} dicts authorizing
  which data sources the dashboard's batch-query endpoint can access.
query_token: optional opaque token required by the batch-query endpoint
  when query_token_required is True.
query_token_required: when True, every request to /api/dashboard-query/{slug}/batch
  must supply the matching token.
dashboard_query_log: per-query audit log written by the batch endpoint.

Revision ID: 034_dashboard_live_query
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "034_dashboard_live_query"
down_revision = "033_dashboard_deployed_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column("query_scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "dashboards",
        sa.Column("query_token", sa.Text, nullable=True),
    )
    op.add_column(
        "dashboards",
        sa.Column("query_token_required", sa.Boolean, nullable=False, server_default="false"),
    )

    op.create_table(
        "dashboard_query_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("ip", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=True),
        sa.Column("property_id", sa.Text, nullable=True),
        sa.Column("cache_hit", sa.Boolean, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_dashboard_query_log_slug_time",
        "dashboard_query_log",
        ["slug", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_dashboard_query_log_slug_time", table_name="dashboard_query_log")
    op.drop_table("dashboard_query_log")
    op.drop_column("dashboards", "query_token_required")
    op.drop_column("dashboards", "query_token")
    op.drop_column("dashboards", "query_scopes")
