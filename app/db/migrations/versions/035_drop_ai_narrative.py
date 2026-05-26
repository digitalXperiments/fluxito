"""035 — Drop abandoned ai_narrative_endpoint / ai_narrative_key_enc columns.

The narrative-endpoint feature was removed in commit e8ef03a ("remove legacy
JS module artifact system — artifact_js, narrative endpoint, style_guide
tool") but its Project columns, API field, settings-page form, and write
path were never cleaned up. Nothing ever read these columns, so dropping
them is safe. The downgrade re-adds them empty.

Revision ID: 035_drop_ai_narrative
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "035_drop_ai_narrative"
down_revision = "034_dashboard_live_query"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("projects", "ai_narrative_key_enc")
    op.drop_column("projects", "ai_narrative_endpoint")


def downgrade() -> None:
    op.add_column("projects", sa.Column("ai_narrative_endpoint", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("ai_narrative_key_enc", sa.Text(), nullable=True))
