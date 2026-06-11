"""
Auditing Platform — SQLAlchemy Models
=======================================

ORM models for the four tables created in migration 052_auditing_platform:

  AuditRun        — One row per audit execution (tag_audit, live_tag_test, etc.)
  AuditFinding    — Individual per-param / per-rule findings for a run
  TagCustomRule   — Project-specific custom audit rules
  LttTestPlan     — Saved live tag test plans

Follows the same pattern as app/models/sdr.py.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ---------------------------------------------------------------------------
# AuditRun
# ---------------------------------------------------------------------------


class AuditRun(Base):
    """One row per audit run (tag audit, live tag test, etc.)."""

    __tablename__ = "audit_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    audit_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="complete")
    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False, server_default="claude")
    url_tested: Mapped[str | None] = mapped_column(Text, nullable=True)
    ltt_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sdr_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationship to findings
    findings: Mapped[list["AuditFinding"]] = relationship(
        "AuditFinding",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        CheckConstraint(
            "audit_type IN ('tag_audit','live_tag_test','data_quality','sdr_compliance',"
            "'platform_health','seo','warehouse','full_suite')",
            name="ck_audit_runs_type",
        ),
        CheckConstraint("status IN ('running','complete','error')", name="ck_audit_runs_status"),
        CheckConstraint(
            "triggered_by IN ('claude','schedule','manual')",
            name="ck_audit_runs_triggered_by",
        ),
    )

    def to_dict(self, include_findings: bool = False) -> dict:
        d = {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "audit_type": self.audit_type,
            "title": self.title,
            "score": self.score,
            "critical": self.critical_count,
            "warning": self.warning_count,
            "info": self.info_count,
            "passed": self.passed_count,
            "status": self.status,
            "triggered_by": self.triggered_by,
            "url_tested": self.url_tested,
            "ltt_session_id": self.ltt_session_id,
            "raw_summary": self.raw_summary,
            "created_by": str(self.created_by),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "duration_ms": self.duration_ms,
        }
        if include_findings and self.findings:
            d["findings"] = [f.to_dict() for f in self.findings]
        return d


# ---------------------------------------------------------------------------
# AuditFinding
# ---------------------------------------------------------------------------


class AuditFinding(Base):
    """One row per individual finding in an audit run."""

    __tablename__ = "audit_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    expected: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actual: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, server_default="rule_book")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    run: Mapped["AuditRun"] = relationship("AuditRun", back_populates="findings")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "platform": self.platform,
            "severity": self.severity,
            "rule_id": self.rule_id,
            "event": self.event,
            "passed": self.passed,
            "message": self.message,
            "remediation": self.remediation,
            "expected": self.expected,
            "actual": self.actual,
            "source": self.source,
            "domain": self.domain,
        }


# ---------------------------------------------------------------------------
# TagCustomRule
# ---------------------------------------------------------------------------


class TagCustomRule(Base):
    """Project-specific custom audit rule."""

    __tablename__ = "tag_custom_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, server_default="*")
    event: Mapped[str] = mapped_column(String(128), nullable=False, server_default="*")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_params: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    forbidden_params: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    param_assertions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, server_default="warning")
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_id", "rule_id", name="uq_tag_custom_rules_project_rule"),
        CheckConstraint("severity IN ('critical','warning','info')", name="ck_tag_custom_rules_severity"),
    )


# ---------------------------------------------------------------------------
# LttTestPlan
# ---------------------------------------------------------------------------


class LttTestPlan(Base):
    """Saved live tag test plan."""

    __tablename__ = "ltt_test_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url_patterns: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    interaction_steps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    expected_platforms: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
