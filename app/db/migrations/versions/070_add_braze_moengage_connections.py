"""070 — add_braze_moengage_connections: credential tables for Braze and MoEngage.

Creates ``braze_connections`` and ``moengage_connections`` tables following the
same credential-connection pattern established by prior migrations:
- id (UUID PK)
- project_id (nullable FK to projects)
- user_id (FK to users)
- display_name
- API-key / app-specific columns
- connection_status, is_active
- created_at, updated_at
- compound index on (project_id, user_id, is_active)

Revision ID: 070_braze_moengage
Revises: 069_branch_appsflyer_adjust
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "070_braze_moengage"
down_revision = "069_branch_appsflyer_adjust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create braze_connections table
    op.create_table(
        "braze_connections",
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
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("rest_endpoint_url", sa.String(255), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_braze_project_user_active", "braze_connections", ["project_id", "user_id", "is_active"]
    )

    # Create moengage_connections table
    op.create_table(
        "moengage_connections",
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
        sa.Column("app_id", sa.String(255), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("data_center", sa.String(32), nullable=False),
        sa.Column("connection_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_moengage_project_user_active", "moengage_connections", ["project_id", "user_id", "is_active"]
    )


def downgrade() -> None:
    op.drop_index("idx_moengage_project_user_active", table_name="moengage_connections")
    op.drop_table("moengage_connections")

    op.drop_index("idx_braze_project_user_active", table_name="braze_connections")
    op.drop_table("braze_connections")
