"""notifications table for in-app notification center

Adds a notifications table to store user notifications across
platform connections, dashboard events, billing changes, and
system alerts. Supports unread tracking and categorization.

Revision ID: 011_notifications
Revises: 010_billing_v2_orgs
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '011_notifications'
down_revision = '010_billing_v2_orgs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category', sa.String(32), nullable=False, server_default='system'),
        sa.Column('severity', sa.String(16), nullable=False, server_default='info'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('action_url', sa.String(512), nullable=True),
        sa.Column('is_read', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])
    op.create_index('ix_notifications_user_unread', 'notifications',
                    ['user_id', 'is_read'],
                    postgresql_where=sa.text('is_read = false'))
    op.create_index('ix_notifications_created_at', 'notifications',
                    ['user_id', 'created_at'])

    # Add display_name column to users if not exists (for profile page)
    # (display_name already exists from initial schema, so skip)


def downgrade() -> None:
    op.drop_index('ix_notifications_created_at', table_name='notifications')
    op.drop_index('ix_notifications_user_unread', table_name='notifications')
    op.drop_index('ix_notifications_user_id', table_name='notifications')
    op.drop_table('notifications')
