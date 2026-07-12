"""071 — add_user_flux_preferences: onboarding role/monitors/AI-client preferences.

Adds three nullable columns to ``users`` so the Ledger onboarding wizard's
"Role & goals" and "Add your AI" steps have somewhere real to persist
choices (surfaced later in Profile -> "How Flux briefs you"):

- flux_role: which of the 4 role cards the user picked
- flux_monitors: JSON list of monitor-chip keys the user selected
- preferred_ai_client: which AI client card the user picked

Revision ID: 071_user_flux_prefs
Revises: 070_braze_moengage
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "071_user_flux_prefs"
down_revision = "070_braze_moengage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("flux_role", sa.String(32), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "flux_monitors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column("users", sa.Column("preferred_ai_client", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferred_ai_client")
    op.drop_column("users", "flux_monitors")
    op.drop_column("users", "flux_role")
