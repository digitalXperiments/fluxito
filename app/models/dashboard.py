"""
Dashboard Models

A dashboard is a named collection of cards. Each card stores the query
parameters that produced it, a chart_type + chart_config spec for frontend
rendering, and a result_cache for fallback rendering.

Dashboards are owned by a user (user_id FK) and also store the owner's
email and display name denormalised — so the public share page can show
"Shared by Ram" without an extra DB join at render time.

Sharing is controlled by is_public + share_slug. The full share_url is
stored on the row so it is queryable and stable.

Structure:
  Dashboard (1) → DashboardCard (many)
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# Soft sanity caps
DASHBOARD_MAX_PER_USER = 10
CARD_MAX_PER_DASHBOARD = 20


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Project that owns this dashboard
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,  # nullable during migration backfill
        index=True,
    )

    # Creator FK (the user who created this dashboard)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised owner info — avoids join on public share page
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Live-query authorization
    query_scopes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    query_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_token_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Custom filter preset chips rendered in the live dashboard UI.
    # Each entry: {"label": "Year 2024", "start": "2024-01-01", "end": "2024-12-31"}
    filter_presets: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))

    # Dashboard-level filter declarations (the six widget types). Each entry:
    #   {"key", "label", "type": date_range|single_select|multi_select|search|
    #    number_range|toggle, "options"?, "toggle"?, "default", "ui"}
    # Empty => the live route synthesizes filters from legacy per-card filter_hooks.
    # See app/dashboards/filter_specs.py for the validated/normalized shape.
    filters: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))

    # Sharing
    share_slug: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    share_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    shared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    cards: Mapped[list["DashboardCard"]] = relationship(
        "DashboardCard",
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardCard.position",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Dashboard(id={self.id}, title={self.title}, is_public={self.is_public})>"


class DashboardCard(Base):
    __tablename__ = "dashboard_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # ga4 | meta | gtm | …
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)  # analytics_read | …

    # The exact params used to produce this card — used to regenerate live data
    query_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Chart spec — type (e.g. "bar", "line", "kpi", "table") and config payload
    chart_type: Mapped[str | None] = mapped_column(String, nullable=True)
    chart_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Cached last result — used as fallback if API is temporarily unavailable
    result_cache: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    dashboard: Mapped["Dashboard"] = relationship("Dashboard", back_populates="cards", lazy="selectin")

    def __repr__(self) -> str:
        return f"<DashboardCard(dashboard_id={self.dashboard_id}, platform={self.platform})>"


class DashboardQueryLog(Base):
    __tablename__ = "dashboard_query_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<DashboardQueryLog(slug={self.slug}, platform={self.platform}, cache_hit={self.cache_hit})>"
