"""
Solution Design Reference (SDR) models.

A Solution Design Reference is the canonical document that answers:
"What events should fire, when, with what parameters, to which destinations,
and why?"

One SDR per project (MVP). Markdown is the source of truth. Structured
projections (SDREvent, SDRParameter, SDRDestination) are rebuilt on every
save for fast queries by audit tools.

Tables:
  sdrs                  — Main record, one per project.
  sdr_versions          — Immutable approved-version snapshots.
  sdr_events            — Projection: events parsed from markdown.
  sdr_parameters        — Projection: parameters per event.
  sdr_destinations      — Projection: per-event destination mappings.
  sdr_refinement_state  — Resumable refinement conversation state.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ---------------------------------------------------------------------------
# Valid status / trigger / event-status values
# ---------------------------------------------------------------------------
SDR_STATUSES = ("draft", "approved", "archived")
EVENT_STATUSES = ("planned", "implemented", "verified", "deprecated")
TRIGGER_TYPES = ("pageview", "click", "form_submit", "datalayer_event", "scroll", "timer", "custom")


class SDR(Base):
    """
    Main SDR record — one per project (MVP).

    ``markdown_content`` is the live draft. ``current_version_id`` points to
    the latest approved snapshot in ``sdr_versions``.
    """

    __tablename__ = "sdrs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdr_versions.id", name="fk_sdr_current_version"),
        nullable=True,
    )
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    intake_answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    intake_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_full_source_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Raw structured source scans used for the current draft (reproducibility).
    last_source_scan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_xlsx: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_xlsx_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_xlsx_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Relationships
    versions: Mapped[list["SDRVersion"]] = relationship(
        "SDRVersion",
        back_populates="sdr",
        cascade="all, delete-orphan",
        foreign_keys="SDRVersion.sdr_id",
    )
    events: Mapped[list["SDREvent"]] = relationship(
        "SDREvent",
        back_populates="sdr",
        cascade="all, delete-orphan",
    )
    refinement_state: Mapped[Optional["SDRRefinementState"]] = relationship(
        "SDRRefinementState",
        back_populates="sdr",
        uselist=False,
        cascade="all, delete-orphan",
    )
    intakes: Mapped[list["SDRIntake"]] = relationship(
        "SDRIntake",
        back_populates="sdr",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved', 'archived')", name="ck_sdr_status"),
        UniqueConstraint("project_id", name="uq_sdr_per_project"),
    )

    def to_dict(self, include_markdown: bool = False) -> dict:
        d = {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "name": self.name,
            "status": self.status,
            "current_version_id": str(self.current_version_id) if self.current_version_id else None,
            "intake_version": self.intake_version,
            "last_full_source_scan_at": self.last_full_source_scan_at.isoformat()
            if self.last_full_source_scan_at
            else None,
            "source_fingerprint": self.source_fingerprint,
            "draft_version": self.draft_version,
            "parsed_at": self.parsed_at.isoformat() if self.parsed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": str(self.created_by),
        }
        if include_markdown:
            d["markdown_content"] = self.markdown_content
            d["intake_answers"] = self.intake_answers
        return d

    def to_full_dict(self) -> dict:
        """Full representation including markdown and parsed projections."""
        return {
            **self.to_dict(include_markdown=True),
            "parsed": {
                "events": [e.to_dict() for e in (self.events or [])],
            },
        }


class SDRVersion(Base):
    """
    Immutable approved version snapshot.

    Created on ``refine_sdr(action="finalize")``. The ``markdown_snapshot``
    is a frozen copy of ``sdrs.markdown_content`` at approval time.
    """

    __tablename__ = "sdr_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sdr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdrs.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    sdr: Mapped["SDR"] = relationship("SDR", back_populates="versions", foreign_keys=[sdr_id])

    __table_args__ = (UniqueConstraint("sdr_id", "version_number", name="uq_sdr_version"),)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "sdr_id": str(self.sdr_id),
            "version_number": self.version_number,
            "changelog": self.changelog,
            "approved_by": str(self.approved_by),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
        }


class SDRIntake(Base):
    """Versioned intake answers used to synthesize an SDR draft."""

    __tablename__ = "sdr_intakes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sdr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdrs.id", ondelete="CASCADE"),
        nullable=False,
    )
    intake_version: Mapped[str] = mapped_column(Text, nullable=False)
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    sdr: Mapped["SDR"] = relationship("SDR", back_populates="intakes")

    __table_args__ = (UniqueConstraint("sdr_id", "intake_version", name="uq_sdr_intake_version"),)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "sdr_id": str(self.sdr_id),
            "project_id": str(self.project_id),
            "intake_version": self.intake_version,
            "answers": self.answers,
            "answered_by": str(self.answered_by) if self.answered_by else None,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
        }


class SDREvent(Base):
    """
    Projection: a single event extracted from SDR markdown.

    Rebuilt on every save — never edited directly. Used by audit tools
    for fast lookups ("what parameters should `purchase` have?").
    """

    __tablename__ = "sdr_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sdr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdrs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    owner_business: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_technical: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_required: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    kpi_links: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Relationships
    sdr: Mapped["SDR"] = relationship("SDR", back_populates="events")
    parameters: Mapped[list["SDRParameter"]] = relationship(
        "SDRParameter",
        back_populates="event",
        cascade="all, delete-orphan",
    )
    destinations: Mapped[list["SDRDestination"]] = relationship(
        "SDRDestination",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("sdr_id", "name", name="uq_sdr_event_name"),
        CheckConstraint(
            "status IS NULL OR status IN ('planned', 'implemented', 'verified', 'deprecated')",
            name="ck_sdr_event_status",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "purpose": self.purpose,
            "trigger_type": self.trigger_type,
            "trigger_config": self.trigger_config,
            "status": self.status,
            "owner_business": self.owner_business,
            "owner_technical": self.owner_technical,
            "consent_required": self.consent_required,
            "kpi_links": self.kpi_links,
            "parameters": [p.to_dict() for p in (self.parameters or [])],
            "destinations": [d.to_dict() for d in (self.destinations or [])],
        }


class SDRParameter(Base):
    """Projection: a single parameter on an SDR event."""

    __tablename__ = "sdr_parameters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdr_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_rule: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    event: Mapped["SDREvent"] = relationship("SDREvent", back_populates="parameters")

    __table_args__ = (UniqueConstraint("event_id", "name", name="uq_sdr_param_name"),)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "source": self.source,
            "example": self.example,
            "validation_rule": self.validation_rule,
        }


class SDRDestination(Base):
    """Projection: a destination mapping for an SDR event."""

    __tablename__ = "sdr_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdr_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    platform_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dest_event_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    event: Mapped["SDREvent"] = relationship("SDREvent", back_populates="destinations")

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "platform_account_id": self.platform_account_id,
            "dest_event_name": self.dest_event_name,
            "mapping": self.mapping,
        }


class SDRRefinementState(Base):
    """
    Resumable refinement conversation state.

    Tracks which section the user is on, what's been completed, and any
    pending proposed changes awaiting user confirmation.
    """

    __tablename__ = "sdr_refinement_state"

    sdr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sdrs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_section: Mapped[str] = mapped_column(Text, nullable=False)
    sections_completed: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    pending_proposed_changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    sdr: Mapped["SDR"] = relationship("SDR", back_populates="refinement_state")

    def to_dict(self) -> dict:
        return {
            "sdr_id": str(self.sdr_id),
            "current_section": self.current_section,
            "sections_completed": self.sections_completed,
            "has_pending_changes": self.pending_proposed_changes is not None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
        }
