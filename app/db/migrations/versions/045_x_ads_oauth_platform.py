"""Add X Ads OAuth app platform.

Revision ID: 045_x_ads_oauth_platform
Revises: 044_superadmin_access_requests
"""

from alembic import op

revision = "045_x_ads_oauth_platform"
down_revision = "044_superadmin_access_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_oauth_app_credentials_platform_valid", "oauth_app_credentials", type_="check")
    op.create_check_constraint(
        "ck_oauth_app_credentials_platform_valid",
        "oauth_app_credentials",
        "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest', 'x')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_oauth_app_credentials_platform_valid", "oauth_app_credentials", type_="check")
    op.create_check_constraint(
        "ck_oauth_app_credentials_platform_valid",
        "oauth_app_credentials",
        "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest')",
    )
