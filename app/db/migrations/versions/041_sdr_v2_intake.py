"""SDR v2 intake and source scan metadata.

Revision ID: 041_sdr_v2_intake
Revises: 040_app_settings
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "041_sdr_v2_intake"
down_revision = "040_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sdrs", sa.Column("intake_answers", JSONB, nullable=True))
    op.add_column("sdrs", sa.Column("intake_version", sa.Text(), nullable=True))
    op.add_column("sdrs", sa.Column("last_full_source_scan_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sdrs", sa.Column("source_fingerprint", sa.Text(), nullable=True))
    op.add_column("sdrs", sa.Column("draft_version", sa.Text(), nullable=True))
    op.add_column("sdrs", sa.Column("last_source_scan", JSONB, nullable=True))

    op.create_table(
        "sdr_intakes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sdr_id", UUID(as_uuid=True), sa.ForeignKey("sdrs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intake_version", sa.Text(), nullable=False),
        sa.Column("answers", JSONB, nullable=False),
        sa.Column("answered_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("sdr_id", "intake_version", name="uq_sdr_intake_version"),
    )
    op.create_index("idx_sdr_intakes_project", "sdr_intakes", ["project_id"])
    op.create_index("idx_sdr_intakes_sdr", "sdr_intakes", ["sdr_id"])


def downgrade() -> None:
    op.drop_index("idx_sdr_intakes_sdr", table_name="sdr_intakes")
    op.drop_index("idx_sdr_intakes_project", table_name="sdr_intakes")
    op.drop_table("sdr_intakes")
    op.drop_column("sdrs", "last_source_scan")
    op.drop_column("sdrs", "draft_version")
    op.drop_column("sdrs", "source_fingerprint")
    op.drop_column("sdrs", "last_full_source_scan_at")
    op.drop_column("sdrs", "intake_version")
    op.drop_column("sdrs", "intake_answers")
