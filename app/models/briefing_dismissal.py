"""Home briefing card dismissals — Ledger revamp Phase 1.2.

When a user clicks "Dismiss" on an urgent/watch briefing card, we persist a
row here so `home()` can exclude that finding from future briefings without
mutating the underlying audit finding (or, for synthetic findings like the
tracking-plan-gap card, without a real row to mutate at all). Scoped to
(user_id, project_id, finding_key) so a dismissal in one project never hides
a finding in another.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class BriefingDismissal(Base):
    """One row per (user, project, finding) a user has dismissed from Briefing."""

    __tablename__ = "briefing_dismissals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stable identifier for the dismissed finding: the AuditFinding UUID
    # (as a string) for real audit findings, or a stable hash/slug for
    # synthetic findings (e.g. "tp-gap") that have no backing row.
    finding_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id", "project_id", "finding_key", name="uq_briefing_dismissal_user_project_finding"
        ),
    )
