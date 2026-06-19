"""Live AI model catalog — synced from vendor APIs, merged with built-in metadata + superadmin extras."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AiCatalogModel(Base):
    """One model entry in the catalog, sourced from vendor API, built-in metadata, or superadmin extras."""

    __tablename__ = "ai_catalog_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capabilities: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="builtin")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "model_id", "source", name="uq_catalog_provider_model_source"),
    )

    def __repr__(self) -> str:
        return f"<AiCatalogModel({self.provider}/{self.model_id} [{self.source}])>"
