"""027 — Drop legacy user/template columns superseded by projects.

Two column drops tied to the projects cleanup (see migration 021):

  * ``users.is_super_admin`` — superseded by ``users.admin_role`` in
    migration 013. All read paths were rewritten to use ``admin_role``
    exclusively in the same PR that introduces this migration, so the
    boolean flag is dead weight.
  * ``templates.user_id`` — superseded by ``templates.project_id`` in
    migration 021. The ``/templates?mine=1`` filter now scopes by the
    active project instead of the creator user.

The legacy tables ``organizations``, ``org_members``, and ``user_plans``
were already dropped in migration 021 — no table drops are needed here.

The downgrade path recreates the columns (without data) so that an
operator can structurally roll back. It does NOT restore original data —
that requires a full DB restore from before the upgrade ran.

Revision ID: 027_drop_legacy_user_flags
Revises: 026_playbooks
Create Date: 2026-04-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "027_drop_legacy_user_flags"
down_revision = "026_playbooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # templates.user_id — drop FK, index, then column.
    # ------------------------------------------------------------------
    # Index name follows SQLAlchemy's default for ``index=True``.
    # FK name follows Alembic's default ``{table}_{col}_fkey`` convention,
    # which is what migration 012_templates_cross_platform created.
    # IF EXISTS everywhere so a partial re-run on a half-migrated DB still
    # converges without manual cleanup.
    op.execute("ALTER TABLE templates DROP CONSTRAINT IF EXISTS templates_user_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_templates_user_id")
    with op.batch_alter_table("templates") as batch:
        batch.drop_column("user_id")

    # ------------------------------------------------------------------
    # users.is_super_admin — plain column drop, no FKs.
    # ------------------------------------------------------------------
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_super_admin")


def downgrade() -> None:
    # Re-add users.is_super_admin (default false). Data is NOT restored;
    # operator must re-run the super-admin sync if any rollback relies on
    # the legacy flag path (no current code does).
    op.add_column(
        "users",
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Re-add templates.user_id (nullable) + FK + index, matching migration
    # 012's original shape.
    op.add_column(
        "templates",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_templates_user_id",
        "templates",
        ["user_id"],
    )
