"""053 — Add filter_presets JSONB column to dashboards.

Stores custom date/filter preset chips that are rendered in the live
dashboard UI (e.g. "Year 2024", "Year 2025"). Each preset is a dict:
  {"label": "Year 2024", "start": "2024-01-01", "end": "2024-12-31"}

Defaults to an empty JSON array so existing dashboards are unaffected.

Revision ID: 053_dashboard_filter_presets
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "053_dashboard_filter_presets"
down_revision = "052_auditing_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column(
            "filter_presets",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("dashboards", "filter_presets")
