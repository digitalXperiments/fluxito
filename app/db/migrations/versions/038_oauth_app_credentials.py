"""OAuth app credentials — install-wide OAuth client IDs/secrets in DB.

Creates `oauth_app_credentials`. One row per platform per install
(unique on platform). The client_secret is Fernet-encrypted with
TOKEN_ENCRYPTION_KEY at the application layer, same as user OAuth
tokens in `oauth_connections`.

Revision ID: 038_oauth_app_credentials
Revises: 037_drop_billing
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "038_oauth_app_credentials"
down_revision = "037_drop_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_app_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("platform", sa.String(32), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret", sa.LargeBinary, nullable=False),
        sa.Column("extra_json", postgresql.JSONB, nullable=True),
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
            "configured_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest')",
            name="ck_oauth_app_credentials_platform_valid",
        ),
    )


def downgrade() -> None:
    op.drop_table("oauth_app_credentials")
