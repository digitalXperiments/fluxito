"""super_admin: add is_super_admin to users table

Revision ID: 007_super_admin
Revises: 006_dashboard_owner_fields
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '007_super_admin'
down_revision = '006_dashboard_owner_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_super_admin flag to users
    op.add_column(
        'users',
        sa.Column('is_super_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )

    # Grant super admin to ramnew2006@gmail.com (if the account already exists)
    op.execute("""
        UPDATE users
        SET is_super_admin = true
        WHERE email = 'ramnew2006@gmail.com'
    """)


def downgrade() -> None:
    op.drop_column('users', 'is_super_admin')
