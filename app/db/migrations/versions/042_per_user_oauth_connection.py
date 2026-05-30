"""Per-user OAuth connections: same external account connectable by many users.

Changes the oauth_connections uniqueness from project-scoped
``(project_id, provider, google_email)`` to user-scoped
``(user_id, project_id, provider, google_email)`` so two different Fluxito
users can each connect the SAME external account (e.g. the same Meta ad
account or the same Google login) within one project and store their own
separate credentials side by side.

Widening a unique key (adding a column) can only ever permit MORE rows, so
no existing row can violate the new constraint — safe, no data backfill.

Note: downgrade re-imposes the narrower project-scoped constraint and will
fail if, by then, two users have connected the same account in one project
(which is exactly what this migration enables). De-duplicate first if you
ever need to roll back.

Revision ID: 042_per_user_oauth_connection
Revises: 041_sdr_v2_intake
"""
from alembic import op

revision = "042_per_user_oauth_connection"
down_revision = "041_sdr_v2_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_project_provider_email", "oauth_connections", type_="unique")
    op.create_unique_constraint(
        "uq_user_project_provider_email",
        "oauth_connections",
        ["user_id", "project_id", "provider", "google_email"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_project_provider_email", "oauth_connections", type_="unique")
    op.create_unique_constraint(
        "uq_project_provider_email",
        "oauth_connections",
        ["project_id", "provider", "google_email"],
    )
