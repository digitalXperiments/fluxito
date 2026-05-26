"""initial schema: users, mcp_auth_codes, mcp_sessions, mcp_clients,
oauth_connections, ga4_properties, gtm_containers, google_ads_accounts

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )

    # mcp_clients
    op.create_table(
        'mcp_clients',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('client_id', sa.String(255), nullable=False),
        sa.Column('client_name', sa.String(255), nullable=False),
        sa.Column('redirect_uris', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('allowed_scopes', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id'),
    )

    # mcp_auth_codes
    op.create_table(
        'mcp_auth_codes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('code', sa.String(255), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', sa.String(255), nullable=False),
        sa.Column('redirect_uri', sa.String(), nullable=False),
        sa.Column('code_challenge', sa.String(255), nullable=True),
        sa.Column('code_challenge_method', sa.String(10), nullable=True),
        sa.Column('scopes', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    # mcp_sessions
    op.create_table(
        'mcp_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('access_token_hash', sa.String(255), nullable=False),
        sa.Column('refresh_token_hash', sa.String(255), nullable=True),
        sa.Column('client_id', sa.String(255), nullable=True),
        sa.Column('scopes', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('access_token_expires_at', sa.DateTime(), nullable=False),
        sa.Column('refresh_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_revoked', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_token_hash'),
        sa.UniqueConstraint('refresh_token_hash'),
    )

    # oauth_connections
    op.create_table(
        'oauth_connections',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(50), nullable=False, server_default='google'),
        sa.Column('google_email', sa.String(255), nullable=True),
        sa.Column('access_token_encrypted', sa.Text(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=False),
        sa.Column('token_expiry', sa.DateTime(), nullable=True),
        sa.Column('scopes', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('connection_status', sa.String(50), nullable=False, server_default='active'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'provider', 'google_email', name='uq_user_provider_email'),
    )

    # ga4_properties
    op.create_table(
        'ga4_properties',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('property_id', sa.String(50), nullable=False),
        sa.Column('property_name', sa.String(255), nullable=True),
        sa.Column('account_id', sa.String(50), nullable=True),
        sa.Column('account_name', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['connection_id'], ['oauth_connections.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # gtm_containers
    op.create_table(
        'gtm_containers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', sa.String(50), nullable=False),
        sa.Column('container_id', sa.String(50), nullable=False),
        sa.Column('container_name', sa.String(255), nullable=True),
        sa.Column('public_id', sa.String(50), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['connection_id'], ['oauth_connections.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # google_ads_accounts
    op.create_table(
        'google_ads_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', sa.String(50), nullable=False),
        sa.Column('account_name', sa.String(255), nullable=True),
        sa.Column('currency_code', sa.String(10), nullable=True),
        sa.Column('timezone', sa.String(100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['connection_id'], ['oauth_connections.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Pre-seed Claude.ai MCP client
    op.execute("""
        INSERT INTO mcp_clients (client_id, client_name, redirect_uris, allowed_scopes, is_public)
        VALUES (
            'claude-ai',
            'Claude.ai',
            ARRAY['https://claude.ai/api/mcp/auth_callback'],
            ARRAY['read'],
            TRUE
        )
        ON CONFLICT (client_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('google_ads_accounts')
    op.drop_table('gtm_containers')
    op.drop_table('ga4_properties')
    op.drop_table('oauth_connections')
    op.drop_table('mcp_sessions')
    op.drop_table('mcp_auth_codes')
    op.drop_table('mcp_clients')
    op.drop_table('users')
