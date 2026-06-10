"""MCP PAT support: add kind/name/token_hint to mcp_sessions for headless/remote access tokens.

Revision ID: 051_mcp_pat_support
Revises: 050_rbac_roles
"""
import sqlalchemy as sa
from alembic import op

revision = "051_mcp_pat_support"
down_revision = "050_rbac_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_sessions",
        sa.Column("kind", sa.String(20), nullable=False, server_default="oauth"),
    )
    op.add_column(
        "mcp_sessions",
        sa.Column("name", sa.String(255), nullable=True),
    )
    op.add_column(
        "mcp_sessions",
        sa.Column("token_hint", sa.String(64), nullable=True),
    )

    # Backfill existing OAuth-issued rows (all current rows are OAuth)
    op.execute("UPDATE mcp_sessions SET kind = 'oauth' WHERE kind IS NULL OR kind = ''")


def downgrade() -> None:
    op.drop_column("mcp_sessions", "token_hint")
    op.drop_column("mcp_sessions", "name")
    op.drop_column("mcp_sessions", "kind")
