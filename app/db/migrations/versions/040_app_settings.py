"""General application / system settings table.

Creates `app_settings` for non-bootstrap configuration that previously
lived only in environment variables (SMTP, GCS, Sentry, CORS, rate limits,
feature flags, etc.).

Supports optional Fernet encryption for secret values using the same
TOKEN_ENCRYPTION_KEY as oauth_app_credentials and user OAuth tokens.

Revision ID: 040_app_settings
Revises: 039_repair_platform_indexes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "040_app_settings"
down_revision = "039_repair_platform_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("value_json", postgresql.JSONB, nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_app_settings_key", "app_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_app_settings_key", table_name="app_settings")
    op.drop_table("app_settings")