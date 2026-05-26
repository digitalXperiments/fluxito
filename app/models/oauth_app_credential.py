"""OAuth app credentials — install-wide OAuth client IDs/secrets.

One row per platform. The `client_secret` field stores Fernet
ciphertext; encryption/decryption is handled by
`app.auth.oauth_app_credentials`, not by the model itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

SUPPORTED_PLATFORMS = ("google", "meta", "tiktok", "snap", "linkedin", "pinterest")


class OAuthAppCredential(Base):
    __tablename__ = "oauth_app_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    extra_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    configured_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "platform IN ('google', 'meta', 'tiktok', 'snap', 'linkedin', 'pinterest')",
            name="ck_oauth_app_credentials_platform_valid",
        ),
    )

    def __repr__(self) -> str:
        return f"<OAuthAppCredential(platform={self.platform!r})>"
