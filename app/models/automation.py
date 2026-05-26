"""
Automation Models — Cowork-native scheduled monitor recipes.

An *automation* is a fully self-contained prompt that Claude in Cowork executes
on a cron schedule (via Cowork's `create_scheduled_task` tool). It produces
a digest, runs an audit, watches a metric for anomalies, etc., and can post
the result to Slack or email.

There are two kinds:

  - ``system`` automations are seeded on every app start from
    ``app/db/seed_automations.py``. They are visible to every project and
    cannot be edited by users. Updates flow in via re-deploys.
  - ``user`` automations are authored within a specific project (Pro/Team
    only) and visible only inside that project.

Themes group automations by intent for filtering / badges in the library UI:

  daily_digest    — recurring summary you want to receive on a cadence
  anomaly         — only fires when a metric is meaningfully off
  pacing          — budget / target pacing checks
  exec_summary    — narrative-heavy weekly/monthly rollups
  tag_health      — GTM / GA4 tracking integrity monitors
  launch_monitor  — short-lived watchers for new campaigns / launches

The companion ``AutomationInstallation`` table records each time a user
installs an automation into Cowork. We do *not* run the automation ourselves —
Cowork's scheduler does — so the install row is informational only.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# --------------------------------------------------------------------------- #
# Constants — kept in sync with the Alembic check constraints.
# --------------------------------------------------------------------------- #

AUTOMATION_TYPE_SYSTEM = "system"
AUTOMATION_TYPE_USER = "user"
VALID_AUTOMATION_TYPES = (AUTOMATION_TYPE_SYSTEM, AUTOMATION_TYPE_USER)

THEME_DAILY_DIGEST = "daily_digest"
THEME_ANOMALY = "anomaly"
THEME_PACING = "pacing"
THEME_EXEC_SUMMARY = "exec_summary"
THEME_TAG_HEALTH = "tag_health"
THEME_LAUNCH_MONITOR = "launch_monitor"
VALID_THEMES = (
    THEME_DAILY_DIGEST,
    THEME_ANOMALY,
    THEME_PACING,
    THEME_EXEC_SUMMARY,
    THEME_TAG_HEALTH,
    THEME_LAUNCH_MONITOR,
)

# Friendly labels used by the UI / MCP tool responses.
THEME_LABELS = {
    THEME_DAILY_DIGEST: "Daily Digest",
    THEME_ANOMALY: "Anomaly Watcher",
    THEME_PACING: "Pacing Check",
    THEME_EXEC_SUMMARY: "Executive Summary",
    THEME_TAG_HEALTH: "Tag / Tracking Health",
    THEME_LAUNCH_MONITOR: "Launch Monitor",
}

INSTALL_STATUS_ACTIVE = "active"
INSTALL_STATUS_PAUSED = "paused"
INSTALL_STATUS_REMOVED = "removed"
VALID_INSTALL_STATUSES = (
    INSTALL_STATUS_ACTIVE,
    INSTALL_STATUS_PAUSED,
    INSTALL_STATUS_REMOVED,
)


class Automation(Base):
    """A reusable, prompt-driven Cowork scheduled-task recipe."""

    __tablename__ = "playbooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # NULL for system automations; set for user-authored ones.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)

    playbook_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AUTOMATION_TYPE_USER
    )
    theme: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=THEME_DAILY_DIGEST,
        index=True,
    )
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)

    required_platforms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)

    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    default_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_schedule_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_task_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    channel_hints: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    min_tier: Mapped[str] = mapped_column(String(16), nullable=False, server_default="free")

    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def theme_label(self) -> str:
        return THEME_LABELS.get(self.theme, self.theme.replace("_", " ").title())


class AutomationInstallation(Base):
    """A record that a user installed an automation into Cowork."""

    __tablename__ = "playbook_installations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)

    variable_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    channel_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    rendered_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=INSTALL_STATUS_ACTIVE)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    installed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
