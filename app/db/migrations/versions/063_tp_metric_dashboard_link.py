"""063 — Add dashboard_card_id FK to tp_metrics.

Links a tracking-plan metric to a specific dashboard card so the UI can
show which live card measures each metric and coverage tooling can flag
unmeasured metrics.

Additive: one new nullable column + index on tp_metrics. Reversible.

Revision ID: 063_tp_metric_dashboard_link
Revises: 062_tp_validation_rule
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "063_tp_metric_dashboard_link"
down_revision = "062_tp_validation_rule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tp_metrics",
        sa.Column(
            "dashboard_card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboard_cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tp_metrics_dashboard_card", "tp_metrics", ["dashboard_card_id"])


def downgrade() -> None:
    op.drop_index("ix_tp_metrics_dashboard_card", table_name="tp_metrics")
    op.drop_column("tp_metrics", "dashboard_card_id")
