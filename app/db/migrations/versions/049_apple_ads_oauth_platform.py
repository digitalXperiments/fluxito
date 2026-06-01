"""Add Apple Ads OAuth app platform.

Revision ID: 049_apple_ads_oauth_platform
Revises: 048_marketo_connections

Note: keep revision IDs <= 32 chars — alembic_version.version_num is VARCHAR(32).
"""

from alembic import op

revision = "049_apple_ads_oauth_platform"
down_revision = "048_marketo_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_oauth_app_credentials_platform_valid", "oauth_app_credentials", type_="check")
    op.create_check_constraint(
        "ck_oauth_app_credentials_platform_valid",
        "oauth_app_credentials",
        "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest', 'x', 'reddit', 'bing', 'apple')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_oauth_app_credentials_platform_valid", "oauth_app_credentials", type_="check")
    op.create_check_constraint(
        "ck_oauth_app_credentials_platform_valid",
        "oauth_app_credentials",
        "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest', 'x', 'reddit', 'bing')",
    )
