"""033 — Dashboard deployed_by and artifact_html_improved columns.

deployed_by: tracks which MCP client deployed the artifact (e.g. "claude.ai",
  "chatgpt.com"). Used to show the "Improvise with Claude" button for dashboards
  not deployed by Claude.

artifact_html_improved: stores the Claude-improved version of a non-Claude
  artifact. Served in place of artifact_html when present. Cleared automatically
  when a new artifact is deployed.

Revision ID: 033_dashboard_deployed_by
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "033_dashboard_deployed_by"
down_revision = "032_html_artifact_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("deployed_by", sa.String(100), nullable=True))
    op.add_column("dashboards", sa.Column("artifact_html_improved", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "artifact_html_improved")
    op.drop_column("dashboards", "deployed_by")
