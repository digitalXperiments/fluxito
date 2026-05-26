"""023 — Google Search Console sites.

Adds `search_console_sites` for GSC property discovery, mirroring
`ga4_properties` / `gtm_containers`. Sites are auto-discovered at OAuth
callback time when the user grants a webmasters scope.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "023_search_console"
down_revision = "022_email_password_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_console_sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        # site_url is the canonical GSC identifier, e.g. "https://example.com/"
        # or "sc-domain:example.com" for Domain properties.
        sa.Column("site_url", sa.String(512), nullable=False),
        sa.Column("permission_level", sa.String(32), nullable=True),  # siteOwner / siteFullUser / siteRestrictedUser / siteUnverifiedUser
        sa.Column("is_domain_property", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["oauth_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_console_sites_connection",
        "search_console_sites",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_console_sites_connection", table_name="search_console_sites")
    op.drop_table("search_console_sites")
