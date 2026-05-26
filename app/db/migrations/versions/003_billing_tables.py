"""billing tables: user_plans and usage_ledger

Revision ID: 003_billing_tables
Revises: 002_bq_connections
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '003_billing_tables'
down_revision = '002_bq_connections'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user_plans — one row per user, tracks tier + quota + credits
    op.create_table(
        'user_plans',
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            primary_key=True,
            nullable=False,
        ),
        sa.Column('tier', sa.String(16), nullable=False, server_default='free'),
        sa.Column('queries_included', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('credit_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('plan_started_at', sa.DateTime(), nullable=True),
        sa.Column('current_period_reset', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )

    # usage_ledger — one row per billable tool call
    op.create_table(
        'usage_ledger',
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
            index=True,
        ),
        sa.Column('tool_name', sa.String(64), nullable=False),
        sa.Column('platform', sa.String(32), nullable=True),
        sa.Column('month_key', sa.String(7), nullable=False, index=True),
        sa.Column('billed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )

    # Composite index for the most common query: user + month
    op.create_index(
        'ix_usage_ledger_user_month',
        'usage_ledger',
        ['user_id', 'month_key'],
    )


def downgrade() -> None:
    op.drop_index('ix_usage_ledger_user_month', table_name='usage_ledger')
    op.drop_table('usage_ledger')
    op.drop_table('user_plans')
