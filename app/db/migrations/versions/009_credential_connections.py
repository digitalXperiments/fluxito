"""Add credential-based connections for Amplitude, Adobe, Redshift, and Snowflake.

Revision ID: 009_credential_connections
Revises: 008_dashboard_snapshots
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '009_credential_connections'
down_revision = '008_dashboard_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create amplitude_connections table
    op.create_table(
        'amplitude_connections',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('project_name', sa.String(255), nullable=True),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('secret_key_encrypted', sa.Text(), nullable=False),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create adobe_connections table
    op.create_table(
        'adobe_connections',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('org_id', sa.String(255), nullable=False),
        sa.Column('company_id', sa.String(255), nullable=True),
        sa.Column('client_id_encrypted', sa.Text(), nullable=False),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=False),
        sa.Column('has_analytics', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('has_launch', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create redshift_connections table
    op.create_table(
        'redshift_connections',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('host_encrypted', sa.Text(), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False, server_default='5439'),
        sa.Column('database', sa.String(255), nullable=False),
        sa.Column('username_encrypted', sa.Text(), nullable=False),
        sa.Column('password_encrypted', sa.Text(), nullable=False),
        sa.Column('default_schema', sa.String(255), nullable=False, server_default='public'),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create snowflake_connections table
    op.create_table(
        'snowflake_connections',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('account_encrypted', sa.Text(), nullable=False),
        sa.Column('username_encrypted', sa.Text(), nullable=False),
        sa.Column('password_encrypted', sa.Text(), nullable=False),
        sa.Column('warehouse', sa.String(255), nullable=False),
        sa.Column('database', sa.String(255), nullable=False),
        sa.Column('default_schema', sa.String(255), nullable=False, server_default='PUBLIC'),
        sa.Column('role', sa.String(255), nullable=True),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('snowflake_connections')
    op.drop_table('redshift_connections')
    op.drop_table('adobe_connections')
    op.drop_table('amplitude_connections')
