"""067 — ai_catalog_models: live model catalog synced from vendor APIs.

Creates ``ai_catalog_models`` table to store models fetched from vendor
APIs, merged with built-in metadata and superadmin extras. Each row
carries provider, model_id, display_name, capabilities, source
(builtin/live/extra), and an enabled toggle.

Revision ID: 067_ai_catalog_models
Revises: 066_ai_provider_default
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "067_ai_catalog_models"
down_revision = "066_ai_provider_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_catalog_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False, index=True),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=True),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="builtin",
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "provider", "model_id", "source", name="uq_catalog_provider_model_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_catalog_models")
