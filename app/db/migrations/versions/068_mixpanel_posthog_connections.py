"""068 — mixpanel_posthog_connections: credential tables for Mixpanel and PostHog.

Creates ``mixpanel_connections`` and ``posthog_connections`` tables mirroring
the existing Amplitude/Marketo credential-connection pattern.

Revision ID: 068_mixpanel_posthog_connections
Revises: 067_ai_catalog_models
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "068_mixpanel_posthog_connections"
down_revision = "067_ai_catalog_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Mixpanel ──────────────────────────────────────────────────────────
    op.create_table(
        "mixpanel_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "connection_status",
            sa.String(50),
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_mixpanel_project_user_active",
        "mixpanel_connections",
        ["project_id", "user_id", "is_active"],
    )

    # ── PostHog ──────────────────────────────────────────────────────────
    op.create_table(
        "posthog_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("project_host", sa.String(512), nullable=False),
        sa.Column("external_project_id", sa.String(255), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "connection_status",
            sa.String(50),
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_posthog_project_user_active",
        "posthog_connections",
        ["project_id", "user_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_table("posthog_connections")
    op.drop_table("mixpanel_connections")
