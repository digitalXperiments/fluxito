"""055 — Add cache_ttl_seconds column to dashboards.

Live-data cache TTL per dashboard (seconds). Default 86400 (24h). The live-data
endpoint caches each filter combination for this long and shows a freshness banner;
owners can override it per dashboard.

Revision ID: 055_dashboard_cache_ttl
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "055_dashboard_cache_ttl"
down_revision = "054_dashboard_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column(
            "cache_ttl_seconds",
            sa.Integer(),
            nullable=False,
            server_default="86400",
        ),
    )


def downgrade() -> None:
    op.drop_column("dashboards", "cache_ttl_seconds")
