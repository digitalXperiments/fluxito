"""073 — briefing_dismissals: per-user Home briefing card dismiss/archive.

Backs the Home briefing "Dismiss" action (Ledger revamp Phase 1.2): clicking
Dismiss on an urgent/watch card persists a row here keyed by
(user_id, project_id, finding_key) so `home()` can exclude that finding on
future loads. `finding_key` is the AuditFinding UUID (as text) for real audit
findings, or a stable slug/hash for synthetic findings (e.g. the
tracking-plan-gap card) that have no backing row to soft-delete.

Additive: one new table. Reversible.

Revision ID: 073_briefing_dismissals
Revises: 072_flux_drafts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "073_briefing_dismissals"
down_revision = "072_flux_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "briefing_dismissals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_key", sa.String(length=128), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "project_id", "finding_key", name="uq_briefing_dismissal_user_project_finding"
        ),
    )
    op.create_index("ix_briefing_dismissals_user_id", "briefing_dismissals", ["user_id"])
    op.create_index("ix_briefing_dismissals_project_id", "briefing_dismissals", ["project_id"])


def downgrade() -> None:
    op.drop_table("briefing_dismissals")
