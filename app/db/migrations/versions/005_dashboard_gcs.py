"""Add gcs_path and result_cache to dashboard_cards; drop result_snapshot

Revision ID: 005_dashboard_gcs
Revises: 004_dashboards
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '005_dashboard_gcs'
down_revision = '004_dashboards'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add gcs_path — the GCS object path to the card's Python script
    op.add_column(
        'dashboard_cards',
        sa.Column('gcs_path', sa.String(500), nullable=True),
    )

    # Add result_cache — JSONB cache of last successful execution result
    op.add_column(
        'dashboard_cards',
        sa.Column(
            'result_cache',
            postgresql.JSONB(),
            nullable=False,
            server_default='{}',
        ),
    )

    # Drop the old static result_snapshot column
    op.drop_column('dashboard_cards', 'result_snapshot')


def downgrade() -> None:
    op.add_column(
        'dashboard_cards',
        sa.Column(
            'result_snapshot',
            postgresql.JSONB(),
            nullable=False,
            server_default='{}',
        ),
    )
    op.drop_column('dashboard_cards', 'result_cache')
    op.drop_column('dashboard_cards', 'gcs_path')
