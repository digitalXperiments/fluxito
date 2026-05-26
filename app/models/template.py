"""
Template Library Models

A template is a pre-built prompt/dashboard recipe that users can browse and
one-click deploy. Templates define which tools to call, with what parameters,
and how to arrange the resulting cards into a dashboard.

Types:
  system  — shipped by Fluxito (seeded on first boot, immutable by users)
  user    — created by a user (Pro/Team only)
  shared  — user-created template shared publicly (future)

Categories align with common use-cases:
  ecommerce, ppc, seo, gtm, analytics, cross_channel, warehouse, custom
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Template(Base):
    """
    A reusable dashboard/prompt recipe.

    `steps` is a JSONB array defining the sequence of MCP tool calls:
      [
        {
          "tool": "marketing_read",
          "params": {"platform": "google", "action": "get_campaign_performance", ...},
          "card_title": "Google Ads — Campaign Performance",
          "card_type": "TABLE"
        },
        ...
      ]

    `required_platforms` lists which platforms must be connected to use this
    template (e.g. ["ga4", "google_ads", "meta"]).

    `variables` defines user-fillable placeholders:
      [
        {"key": "date_range_start", "label": "Start Date", "type": "date", "default": "-30d"},
        {"key": "account_id", "label": "Ad Account ID", "type": "string"}
      ]
    """

    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Project — NULL for system templates
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, server_default="custom", index=True)
    # system | user | shared
    template_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")

    # Slug for URL-friendly reference
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)

    # Icon identifier (maps to platform_icon or a generic icon)
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Which platforms are needed (checked against user connections at deploy time)
    required_platforms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # The recipe: ordered list of tool calls + card configs
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # User-fillable variables (dates, account IDs, etc.)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Minimum plan tier required (free | pro | team)
    min_tier: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pro")

    # Popularity / sorting
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
