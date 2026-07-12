"""AI provider keys: allow project-scoped rows.

A row with ``user_id IS NULL`` is a project-shared default key (set by a
project owner/admin). Personal rows (user_id set) override it at resolution
time — see app/ask/keys.py.

Revision ID: 077_project_ai_keys
Revises: 076_test_flows
"""

from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "077_project_ai_keys"
down_revision = "076_test_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ai_provider_keys",
        "user_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Project-shared rows cannot survive a NOT NULL user_id — drop them first.
    op.execute("DELETE FROM ai_provider_keys WHERE user_id IS NULL")
    op.alter_column(
        "ai_provider_keys",
        "user_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
