"""dashboards and dashboard_cards tables

Revision ID: 004_dashboards
Revises: 003_billing_tables
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '004_dashboards'
down_revision = '003_billing_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # dashboards
    op.create_table(
        'dashboards',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('share_slug', sa.String(16), nullable=False, unique=True),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_dashboards_user_id', 'dashboards', ['user_id'])
    op.create_index('ix_dashboards_share_slug', 'dashboards', ['share_slug'])

    # dashboard_cards
    op.create_table(
        'dashboard_cards',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'dashboard_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('dashboards.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('platform', sa.String(32), nullable=False),
        sa.Column('tool_name', sa.String(64), nullable=False),
        sa.Column('query_params', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('result_snapshot', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('refreshed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_dashboard_cards_dashboard_id', 'dashboard_cards', ['dashboard_id'])


def downgrade() -> None:
    op.drop_index('ix_dashboard_cards_dashboard_id', table_name='dashboard_cards')
    op.drop_table('dashboard_cards')
    op.drop_index('ix_dashboards_share_slug', table_name='dashboards')
    op.drop_index('ix_dashboards_user_id', table_name='dashboards')
    op.drop_table('dashboards')
