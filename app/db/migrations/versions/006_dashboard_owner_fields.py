"""Add owner_email, owner_name, share_url, shared_at to dashboards

Revision ID: 006_dashboard_owner_fields
Revises: 005_dashboard_gcs
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '006_dashboard_owner_fields'
down_revision = '005_dashboard_gcs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Denormalised owner info — avoids join on public share page
    op.add_column(
        'dashboards',
        sa.Column('owner_email', sa.String(255), nullable=False, server_default=''),
    )
    op.add_column(
        'dashboards',
        sa.Column('owner_name', sa.String(255), nullable=True),
    )

    # Stored share URL — stable, queryable, no need to compute at render time
    op.add_column(
        'dashboards',
        sa.Column('share_url', sa.String(500), nullable=True),
    )

    # Timestamp of when the dashboard was last made public
    op.add_column(
        'dashboards',
        sa.Column('shared_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dashboards', 'shared_at')
    op.drop_column('dashboards', 'share_url')
    op.drop_column('dashboards', 'owner_name')
    op.drop_column('dashboards', 'owner_email')
