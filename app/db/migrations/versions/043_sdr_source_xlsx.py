"""Add source-xlsx storage columns to sdrs.

Revision ID: 043_sdr_source_xlsx
Revises: 042_per_user_oauth_connection
"""

import sqlalchemy as sa
from alembic import op

revision = "043_sdr_source_xlsx"
down_revision = "042_per_user_oauth_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sdrs", sa.Column("source_xlsx", sa.LargeBinary(), nullable=True))
    op.add_column("sdrs", sa.Column("source_xlsx_filename", sa.Text(), nullable=True))
    op.add_column("sdrs", sa.Column("source_xlsx_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sdrs", "source_xlsx_at")
    op.drop_column("sdrs", "source_xlsx_filename")
    op.drop_column("sdrs", "source_xlsx")
