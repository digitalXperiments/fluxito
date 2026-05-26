"""029 — Composite indexes for hot-path queries.

Three additive indexes driven by the 2026-04-20 optimization report:

  * ``ix_oauth_project_active_provider`` / ``ix_oauth_user_active_provider``
    — `_load_connections_and_resources()` scans `oauth_connections` by
    (project_id|user_id, is_active, provider) on every SSE session start.
    Single-column indexes forced partial table scans; composites let
    Postgres serve the filter directly.
  * ``ix_projects_owner_id`` — `projects.owner_id` is a FK with no index,
    so "list my projects" and the free-project-limit check did full
    table scans.
  * ``ix_audit_user_tool_status_ts`` — audit UI filters/sorts by all four
    columns; per-column indexes didn't cover the composite predicate.

All four are ``IF NOT EXISTS`` so re-runs on a partially-upgraded DB
converge, and all four use ``op.execute`` rather than
``op.create_index(... if_not_exists=True)`` because older Alembic
versions on this project don't accept that kwarg.

Revision ID: 029_optimization_indexes
Revises: 028_sdr_feature
Create Date: 2026-04-20
"""
from alembic import op


revision = "029_optimization_indexes"
down_revision = "028_sdr_feature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oauth_project_active_provider "
        "ON oauth_connections (project_id, is_active, provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oauth_user_active_provider "
        "ON oauth_connections (user_id, is_active, provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_projects_owner_id "
        "ON projects (owner_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_user_tool_status_ts "
        "ON tool_call_audit (user_id, tool_name, status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_user_tool_status_ts")
    op.execute("DROP INDEX IF EXISTS ix_projects_owner_id")
    op.execute("DROP INDEX IF EXISTS ix_oauth_user_active_provider")
    op.execute("DROP INDEX IF EXISTS ix_oauth_project_active_provider")
