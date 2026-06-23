"""068 — add_branch_appsflyer_adjust_connections: credential connections for Branch, AppsFlyer, Adjust.

Creates three new credential connection tables for Mobile Measurement Partner (MMP)
platforms. Each follows the same schema pattern as amplitude_connections:
- id (UUID PK)
- project_id (nullable FK to projects)
- user_id (FK to users)
- display_name, project_name
- api_key_encrypted, secret_key_encrypted
- connection_status, is_active
- created_at, updated_at
- compound index on (project_id, user_id, is_active)

Revision ID: 068_branch_appsflyer_adjust
Revises: 067_ai_catalog_models
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "068_branch_appsflyer_adjust"
down_revision = "067_ai_catalog_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create branch_connections table
    op.create_table(
        "branch_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_branch_project_user_active", "branch_connections", ["project_id", "user_id", "is_active"]
    )

    # Create appsflyer_connections table
    op.create_table(
        "appsflyer_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_appsflyer_project_user_active", "appsflyer_connections", ["project_id", "user_id", "is_active"]
    )

    # Create adjust_connections table
    op.create_table(
        "adjust_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_adjust_project_user_active", "adjust_connections", ["project_id", "user_id", "is_active"]
    )


def downgrade() -> None:
    op.drop_index("idx_adjust_project_user_active", table_name="adjust_connections")
    op.drop_table("adjust_connections")

    op.drop_index("idx_appsflyer_project_user_active", table_name="appsflyer_connections")
    op.drop_table("appsflyer_connections")

    op.drop_index("idx_branch_project_user_active", table_name="branch_connections")
    op.drop_table("branch_connections")
