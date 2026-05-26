"""Repair: fix duplicate index names + create missing platform ad-account tables.

This replaces the abandoned 4e3c85824759 migration (which branched from 027
instead of the current head). All the same DDL, properly sequenced after 038.

Revision ID: 039_repair_platform_indexes
Revises: 038_oauth_app_credentials
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "039_repair_platform_indexes"
down_revision = "038_oauth_app_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_ads_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.String(length=50), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("timezone_name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["oauth_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meta_ads_accounts_account_id", "meta_ads_accounts", ["account_id"])
    op.create_index("ix_meta_ads_accounts_connection_id", "meta_ads_accounts", ["connection_id"])
    op.create_index("ix_meta_ads_accounts_is_active", "meta_ads_accounts", ["is_active"])

    op.create_table(
        "tiktok_ads_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("advertiser_id", sa.String(length=50), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["oauth_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiktok_ads_accounts_advertiser_id", "tiktok_ads_accounts", ["advertiser_id"])
    op.create_index("ix_tiktok_ads_accounts_connection_id", "tiktok_ads_accounts", ["connection_id"])
    op.create_index("ix_tiktok_ads_accounts_is_active", "tiktok_ads_accounts", ["is_active"])

    op.create_table(
        "snap_ads_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.String(length=50), nullable=False),
        sa.Column("account_id", sa.String(length=50), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["oauth_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snap_ads_accounts_organization_id", "snap_ads_accounts", ["organization_id"])
    op.create_index("ix_snap_ads_accounts_account_id", "snap_ads_accounts", ["account_id"])
    op.create_index("ix_snap_ads_accounts_connection_id", "snap_ads_accounts", ["connection_id"])
    op.create_index("ix_snap_ads_accounts_is_active", "snap_ads_accounts", ["is_active"])

    op.execute("DROP INDEX IF EXISTS idx_project_user_active")
    op.create_index("idx_bq_project_user_active", "bq_connections", ["fluxito_project_id", "user_id", "is_active"])
    op.create_index("idx_amplitude_project_user_active", "amplitude_connections", ["project_id", "user_id", "is_active"])
    op.create_index("idx_adobe_project_user_active", "adobe_connections", ["project_id", "user_id", "is_active"])
    op.create_index("idx_redshift_project_user_active", "redshift_connections", ["project_id", "user_id", "is_active"])
    op.create_index("idx_snowflake_project_user_active", "snowflake_connections", ["project_id", "user_id", "is_active"])

    op.execute("DROP INDEX IF EXISTS idx_connection_active")
    op.create_index("idx_ga4_connection_active", "ga4_properties", ["connection_id", "is_active"])

    op.execute("DROP INDEX IF EXISTS idx_connection_account_active")
    op.create_index("idx_gtm_connection_account_active", "gtm_containers", ["connection_id", "account_id", "is_active"])

    op.execute("DROP INDEX IF EXISTS idx_connection_customer_active")
    op.create_index("idx_gads_connection_customer_active", "google_ads_accounts", ["connection_id", "customer_id", "is_active"])

    op.execute("DROP INDEX IF EXISTS idx_connection_site_active")
    op.create_index("idx_gsc_connection_site_active", "search_console_sites", ["connection_id", "site_url", "is_active"])

    op.create_index("idx_meta_connection_account_active", "meta_ads_accounts", ["connection_id", "account_id", "is_active"])
    op.create_index("idx_tiktok_connection_advertiser_active", "tiktok_ads_accounts", ["connection_id", "advertiser_id", "is_active"])
    op.create_index("idx_snap_connection_org_account_active", "snap_ads_accounts", ["connection_id", "organization_id", "account_id", "is_active"])


def downgrade() -> None:
    op.drop_index("idx_snap_connection_org_account_active", table_name="snap_ads_accounts")
    op.drop_index("idx_tiktok_connection_advertiser_active", table_name="tiktok_ads_accounts")
    op.drop_index("idx_meta_connection_account_active", table_name="meta_ads_accounts")
    op.drop_index("idx_gsc_connection_site_active", table_name="search_console_sites")
    op.drop_index("idx_gads_connection_customer_active", table_name="google_ads_accounts")
    op.drop_index("idx_gtm_connection_account_active", table_name="gtm_containers")
    op.drop_index("idx_ga4_connection_active", table_name="ga4_properties")
    op.drop_index("idx_snowflake_project_user_active", table_name="snowflake_connections")
    op.drop_index("idx_redshift_project_user_active", table_name="redshift_connections")
    op.drop_index("idx_adobe_project_user_active", table_name="adobe_connections")
    op.drop_index("idx_amplitude_project_user_active", table_name="amplitude_connections")
    op.drop_index("idx_bq_project_user_active", table_name="bq_connections")
    op.drop_table("snap_ads_accounts")
    op.drop_table("tiktok_ads_accounts")
    op.drop_table("meta_ads_accounts")
