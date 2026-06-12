"""056 — Branch review workflow fields on tp_branches.

Adds the review/approval metadata the branch workflow needs:
- review_status: lifecycle of a branch's review (draft -> ready_for_review ->
  changes_requested / approved), CHECK-constrained.
- reviewer_id: the user assigned to / who acted on the review (nullable FK).
- description: free-text branch summary (nullable).

Revision ID: 056_branch_review_fields
Revises: 055_drop_sdr_tables
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "056_branch_review_fields"
down_revision = "055_drop_sdr_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tp_branches",
        sa.Column(
            "review_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
    )
    op.add_column(
        "tp_branches",
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column("tp_branches", sa.Column("description", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_tp_branch_review_status",
        "tp_branches",
        "review_status IN ('draft', 'ready_for_review', 'changes_requested', 'approved')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tp_branch_review_status", "tp_branches", type_="check")
    op.drop_column("tp_branches", "description")
    op.drop_column("tp_branches", "reviewer_id")
    op.drop_column("tp_branches", "review_status")
