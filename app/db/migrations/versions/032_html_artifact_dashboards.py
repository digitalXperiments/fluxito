"""032 — HTML artifact dashboard system.

Adds artifact_html column for self-contained HTML dashboards.
The old artifact_js / artifact_meta columns are kept (not dropped)
for backwards compatibility with any existing artifact-mode dashboards.
Anthropic API key is app-level via ANTHROPIC_API_KEY env var — no per-project key needed.

Revision ID: 032_html_artifact_dashboards
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "032_html_artifact_dashboards"
down_revision = "031_artifact_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("artifact_html", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "artifact_html")
