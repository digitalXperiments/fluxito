"""bq_connections: BigQuery service-account connections per user

Revision ID: 002_bq_connections
Revises: 001_initial_schema
Create Date: 2026-03-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '002_bq_connections'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bq_connections',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('project_id', sa.String(255), nullable=False),
        sa.Column('datasets', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('service_account_encrypted', sa.Text(), nullable=False),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('bq_connections')
