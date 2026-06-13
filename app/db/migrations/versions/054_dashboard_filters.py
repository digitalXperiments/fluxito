"""054 — Add filters JSONB column to dashboards.

Stores dashboard-level filter declarations for the six widget types
(date_range, single_select, multi_select, search, number_range, toggle).
Each entry is a validated/normalized dict (see app/dashboards/filter_specs.py).

Defaults to an empty JSON array; existing dashboards are unaffected and the
live route synthesizes filters from legacy per-card filter_hooks when empty.

Revision ID: 054_dashboard_filters
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "054_dashboard_filters"
down_revision = "053_dashboard_filter_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column(
            "filters",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("dashboards", "filters")
