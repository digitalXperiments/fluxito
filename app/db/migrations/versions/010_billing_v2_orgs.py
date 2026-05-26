"""billing v2: 3-tier plans, organizations, org_members

Expands billing from free/pro to free/pro/team. Adds organizations
and org_members tables for Team plan multi-user support. Updates
user_plans with Stripe fields, org link, trial support, and new
default quota (1,000 calls/mo for free tier).

Revision ID: 010_billing_v2_orgs
Revises: 009_credential_connections
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '010_billing_v2_orgs'
down_revision = '009_credential_connections'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── organizations table ──
    op.create_table(
        'organizations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(255), unique=True, nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
        sa.Column('billing_cycle', sa.String(16), nullable=True),
        sa.Column('seat_count', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('max_seats', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('max_seats >= 5', name='ck_org_min_seats'),
    )

    # ── org_members table ──
    op.create_table(
        'org_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False, server_default='member'),
        sa.Column('invited_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('invited_at', sa.DateTime(), nullable=True),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true')),
        sa.UniqueConstraint('org_id', 'user_id', name='uq_org_member'),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name='ck_org_member_role'),
    )
    op.create_index('ix_org_members_org_id', 'org_members', ['org_id'])
    op.create_index('ix_org_members_user_id', 'org_members', ['user_id'])

    # ── Expand user_plans ──
    # Add new columns
    op.add_column('user_plans', sa.Column(
        'stripe_customer_id', sa.String(255), nullable=True))
    op.add_column('user_plans', sa.Column(
        'stripe_subscription_id', sa.String(255), nullable=True))
    op.add_column('user_plans', sa.Column(
        'billing_cycle', sa.String(16), nullable=True))
    op.add_column('user_plans', sa.Column(
        'org_id', postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organizations.id', ondelete='SET NULL'),
        nullable=True))
    op.add_column('user_plans', sa.Column(
        'trial_ends_at', sa.DateTime(), nullable=True))

    # Update default queries_included from 50 to 1000 for new free users
    op.alter_column('user_plans', 'queries_included',
                    server_default='1000')


def downgrade() -> None:
    op.drop_column('user_plans', 'trial_ends_at')
    op.drop_column('user_plans', 'org_id')
    op.drop_column('user_plans', 'billing_cycle')
    op.drop_column('user_plans', 'stripe_subscription_id')
    op.drop_column('user_plans', 'stripe_customer_id')

    op.alter_column('user_plans', 'queries_included',
                    server_default='50')

    op.drop_index('ix_org_members_user_id', table_name='org_members')
    op.drop_index('ix_org_members_org_id', table_name='org_members')
    op.drop_table('org_members')
    op.drop_table('organizations')
