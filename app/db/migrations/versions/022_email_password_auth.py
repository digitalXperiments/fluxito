"""
022 — Add email/password auth fields to users table.

New columns:
  - password_hash   VARCHAR(255) NULL — bcrypt hash; NULL for Google-only users
  - email_verified  BOOLEAN NOT NULL DEFAULT FALSE
  - email_verified_at TIMESTAMP NULL
  - auth_provider   VARCHAR(16) NOT NULL DEFAULT 'google'

Existing Google-authenticated users are marked email_verified=TRUE and
auth_provider='google' since their email was already verified by Google.
"""

import sqlalchemy as sa
from alembic import op


revision = "022_email_password_auth"
down_revision = "021_projects"
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns
    op.add_column("users", sa.Column(
        "password_hash", sa.String(255), nullable=True,
    ))
    op.add_column("users", sa.Column(
        "email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.add_column("users", sa.Column(
        "email_verified_at", sa.DateTime(), nullable=True,
    ))
    op.add_column("users", sa.Column(
        "auth_provider", sa.String(16), nullable=False, server_default="google",
    ))

    # Backfill: existing users were Google-authenticated, so mark verified
    op.execute("""
        UPDATE users
        SET email_verified = TRUE,
            email_verified_at = created_at,
            auth_provider = 'google'
        WHERE password_hash IS NULL
    """)


def downgrade():
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "password_hash")
