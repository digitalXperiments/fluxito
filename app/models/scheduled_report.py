"""
Scheduled Reports Models

Four ORM classes:

  * ``ReportSchedule``       — one scheduled delivery of a dashboard (cadence
                               + channels + filters); read by the APScheduler
                               job at run time.
  * ``ReportRun``            — metadata-only audit row per fire. Intentionally
                               stores NO payload: no PDF bytes, no rendered
                               card data, no recipient email addresses. This is
                               the compliance story — we can prove the job ran
                               without retaining what it processed.
  * ``ProjectEmailSender``   — BYO SMTP / SES credentials, per project. The
                               ``config_encrypted`` column is a Fernet-encrypted
                               JSON blob (shape differs per type; see below).
  * ``ProjectSlackWebhook``  — per-project Slack incoming webhooks. The full
                               URL is Fernet-encrypted at rest.

All credential columns use ``app.util.encryption`` (reuses
``TOKEN_ENCRYPTION_KEY``).

Relationship to existing models:
  * ``ReportSchedule.project_id``   → ``projects.id``   (CASCADE)
  * ``ReportSchedule.dashboard_id`` → ``dashboards.id`` (CASCADE)
  * ``ReportRun.schedule_id``       → ``report_schedules.id`` (CASCADE)

No quota enforcement; create as many schedules as you need.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ---------------------------------------------------------------------------
# Cadence / status enums (string constants — PostgreSQL CHECK-constrained)
# ---------------------------------------------------------------------------
CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"
CADENCE_MONTHLY = "monthly"
CADENCE_CUSTOM = "custom_cron"
VALID_CADENCES = {CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY, CADENCE_CUSTOM}

FORMAT_PDF = "pdf"
FORMAT_CSV = "csv"  # reserved
FORMAT_PNG = "png"  # reserved
VALID_FORMATS = {FORMAT_PDF, FORMAT_CSV, FORMAT_PNG}

RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_PARTIAL = "partial"  # some channels failed, some succeeded
VALID_RUN_STATUSES = {
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
}

TRIGGERED_BY_SCHEDULE = "schedule"
TRIGGERED_BY_MANUAL = "manual"

# Auto-disable a schedule after this many consecutive failures
FAILURE_AUTO_DISABLE_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Email sender types
# ---------------------------------------------------------------------------
EMAIL_SENDER_SMTP = "smtp"
EMAIL_SENDER_SES = "ses"
VALID_EMAIL_SENDER_TYPES = {EMAIL_SENDER_SMTP, EMAIL_SENDER_SES}

# Shape of ``ProjectEmailSender.config_encrypted`` (after decrypt_json):
#
#   type = 'smtp':
#     {
#       "host": "smtp.gmail.com",
#       "port": 587,
#       "username": "...",
#       "password": "...",
#       "tls_mode": "starttls"  # 'none' | 'starttls' | 'ssl'
#     }
#
#   type = 'ses':
#     {
#       "region": "us-east-1",
#       "access_key_id": "...",
#       "secret_access_key": "..."
#     }


# ---------------------------------------------------------------------------
# ProjectEmailSender
# ---------------------------------------------------------------------------
class ProjectEmailSender(Base):
    __tablename__ = "project_email_senders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Fernet-encrypted JSON blob — see shape comment above.
    config_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Populated by the "Send test" button in project settings UI
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "type IN ('smtp', 'ses')",
            name="ck_email_sender_type_valid",
        ),
    )

    # ---- convenience: (de)serialize the encrypted config ----
    def get_config(self) -> dict[str, Any]:
        """Decrypt the stored config blob. Raises on tamper / wrong key."""
        from app.utils.encryption import decrypt_json

        return decrypt_json(self.config_encrypted)

    def set_config(self, config: dict[str, Any]) -> None:
        """Encrypt + store a new config blob."""
        from app.utils.encryption import encrypt_json

        self.config_encrypted = encrypt_json(config)


# ---------------------------------------------------------------------------
# ProjectSlackWebhook
# ---------------------------------------------------------------------------
class ProjectSlackWebhook(Base):
    __tablename__ = "project_slack_webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # e.g. "#marketing-daily" — free-text human label
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Full webhook URL, Fernet-encrypted at rest
    webhook_url_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # ---- convenience: (de)serialize the encrypted URL ----
    def get_webhook_url(self) -> str:
        from app.utils.encryption import decrypt_str

        return decrypt_str(self.webhook_url_encrypted)

    def set_webhook_url(self, url: str) -> None:
        from app.utils.encryption import encrypt_str

        self.webhook_url_encrypted = encrypt_str(url)


# ---------------------------------------------------------------------------
# ReportSchedule
# ---------------------------------------------------------------------------
class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # 'daily' | 'weekly' | 'monthly' | 'custom_cron'
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    # Always populated — even preset cadences derive a cron expression at save
    # time so APScheduler reads exactly one field.
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    # IANA timezone, per-schedule
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    # JSONB filter state, possibly containing macros like {{last_7_days}}
    filter_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # List of channel specs. See model docstring for shapes.
    channels: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    format: Mapped[str] = mapped_column(String(16), nullable=False, default=FORMAT_PDF)
    # Scheduled sends exclude AI insights by default (per user decision)
    include_insights: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    runs: Mapped[list[ReportRun]] = relationship(
        "ReportRun",
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ReportRun.started_at.desc()",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "cadence IN ('daily', 'weekly', 'monthly', 'custom_cron')",
            name="ck_report_schedule_cadence_valid",
        ),
        CheckConstraint(
            "format IN ('pdf', 'csv', 'png')",
            name="ck_report_schedule_format_valid",
        ),
        CheckConstraint(
            "last_status IS NULL OR last_status IN ('success', 'failed', 'partial')",
            name="ck_report_schedule_last_status_valid",
        ),
    )


# ---------------------------------------------------------------------------
# ReportRun — metadata-only audit
# ---------------------------------------------------------------------------
class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RUN_STATUS_RUNNING)

    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channels_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channels_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False, default=TRIGGERED_BY_SCHEDULE)

    schedule: Mapped[ReportSchedule] = relationship("ReportSchedule", back_populates="runs", lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')",
            name="ck_report_run_status_valid",
        ),
        CheckConstraint(
            "triggered_by IN ('schedule', 'manual')",
            name="ck_report_run_triggered_by_valid",
        ),
    )
