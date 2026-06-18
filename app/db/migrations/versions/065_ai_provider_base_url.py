"""065 — ai_provider_keys: add base_url column.

Adds an optional ``base_url`` text column to ``ai_provider_keys`` so that
users can configure a custom endpoint for OpenAI-compatible providers (e.g.
LM Studio, self-hosted endpoints). Null means use the registry default.

Revision ID: 065_ai_provider_base_url
Revises: 064_ask_fluxito
"""

import sqlalchemy as sa
from alembic import op

revision = "065_ai_provider_base_url"
down_revision = "064_ask_fluxito"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_provider_keys", sa.Column("base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_provider_keys", "base_url")
