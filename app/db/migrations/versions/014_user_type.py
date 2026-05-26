"""
Add user_type column to users table.

Distinguishes between:
  - 'client': external users who use the product
  - 'team': internal staff / team members

Existing users default to 'client'. The founding admin is set to 'team'.

Revision ID: 014_user_type
Revises: 013_admin_audit_announcements
"""

from alembic import op
import sqlalchemy as sa

revision = "014_user_type"
down_revision = "013_admin_audit_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "user_type",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'client'"),
        ),
    )

    # Mark existing admins / super_admins as team members
    op.execute("""
        UPDATE users
        SET user_type = 'team'
        WHERE admin_role IN ('admin', 'super_admin')
           OR is_super_admin = true
    """)

    # Ensure founding admin is team
    op.execute("""
        UPDATE users
        SET user_type = 'team'
        WHERE email = 'ramnew2006@gmail.com'
    """)


def downgrade() -> None:
    op.drop_column("users", "user_type")
