"""Add dashboard_snapshots table — frozen point-in-time captures.

Revision ID: 008_dashboard_snapshots
Revises: 007_super_admin
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '008_dashboard_snapshots'
down_revision = '007_super_admin'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'dashboard_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'dashboard_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('dashboards.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('owner_email', sa.String(255), nullable=False, server_default=''),
        sa.Column('owner_name', sa.String(255), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('slug', sa.String(24), nullable=False, unique=True),
        sa.Column('snapshot_data', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('filter_params', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('visibility', sa.String(16), nullable=False, server_default='public'),
        sa.Column('password_hash', sa.String(128), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('view_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_dashboard_snapshots_slug', 'dashboard_snapshots', ['slug'], unique=True)
    op.create_index('ix_dashboard_snapshots_user_id', 'dashboard_snapshots', ['user_id'])
    op.create_index('ix_dashboard_snapshots_dashboard_id', 'dashboard_snapshots', ['dashboard_id'])


def downgrade() -> None:
    op.drop_index('ix_dashboard_snapshots_dashboard_id', table_name='dashboard_snapshots')
    op.drop_index('ix_dashboard_snapshots_user_id', table_name='dashboard_snapshots')
    op.drop_index('ix_dashboard_snapshots_slug', table_name='dashboard_snapshots')
    op.drop_table('dashboard_snapshots')
