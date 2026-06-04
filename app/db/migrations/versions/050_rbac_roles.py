# app/db/migrations/versions/050_rbac_roles.py
"""RBAC: roles, member_roles, projects.rbac_enabled.

Revision ID: 050_rbac_roles
Revises: 049_apple_ads_oauth_platform

Note: keep revision IDs <= 32 chars — alembic_version.version_num is VARCHAR(32).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "050_rbac_roles"
down_revision = "049_apple_ads_oauth_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("rbac_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("permissions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name", name="uq_role_project_name"),
    )
    op.create_index("ix_roles_project_active", "roles", ["project_id", "is_active"])
    op.create_table(
        "member_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_member_id", UUID(as_uuid=True), sa.ForeignKey("project_members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("project_member_id", "role_id", name="uq_member_role"),
    )
    op.create_index("ix_member_roles_member", "member_roles", ["project_member_id"])
    op.create_index("ix_member_roles_role", "member_roles", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_member_roles_role", table_name="member_roles")
    op.drop_index("ix_member_roles_member", table_name="member_roles")
    op.drop_table("member_roles")
    op.drop_index("ix_roles_project_active", table_name="roles")
    op.drop_table("roles")
    op.drop_column("projects", "rbac_enabled")
