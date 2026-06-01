"""Add marketo_connections table (Adobe Marketo Engage).

Revision ID: 048_marketo_connections
Revises: 047_bing_oauth_platform

Note: keep revision IDs <= 32 chars — alembic_version.version_num is VARCHAR(32).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "048_marketo_connections"
down_revision = "047_bing_oauth_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketo_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("instance_url", sa.String(512), nullable=False),
        sa.Column("client_id_encrypted", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_marketo_project_user_active", "marketo_connections", ["project_id", "user_id", "is_active"])


def downgrade() -> None:
    op.drop_index("idx_marketo_project_user_active", table_name="marketo_connections")
    op.drop_table("marketo_connections")
