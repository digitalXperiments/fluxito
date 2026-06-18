"""066 — ai_provider_keys: add is_default column.

Adds ``is_default`` boolean column to ``ai_provider_keys`` to designate
which provider Ask Fluxito uses by default when no explicit provider is
requested.

Revision ID: 066_ai_provider_default
Revises: 065_ai_provider_base_url
"""

import sqlalchemy as sa
from alembic import op

revision = "066_ai_provider_default"
down_revision = "065_ai_provider_base_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_keys",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_keys", "is_default")
