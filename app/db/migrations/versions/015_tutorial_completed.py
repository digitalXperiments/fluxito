"""
Add tutorial_completed_at column to users table.

Tracks whether a user has completed the interactive onboarding tutorial.
NULL means they haven't completed it yet; a timestamp means they have.

Revision ID: 015_tutorial_completed
Revises: 014_user_type
"""

from alembic import op
import sqlalchemy as sa

revision = "015_tutorial_completed"
down_revision = "014_user_type"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("tutorial_completed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("users", "tutorial_completed_at")
