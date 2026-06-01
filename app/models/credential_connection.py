import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Constants for connection status (reused across all credential connections)
CONNECTION_STATUS_ACTIVE = "active"
CONNECTION_STATUS_DISCONNECTED = "disconnected"
CONNECTION_STATUS_ERROR = "error"


class AmplitudeConnection(Base):
    """Amplitude API key connection scoped to a project."""

    __tablename__ = "amplitude_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(50), default=CONNECTION_STATUS_ACTIVE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_amplitude_project_user_active", project_id, user_id, is_active),)

    def __repr__(self) -> str:
        return f"<AmplitudeConnection(display_name={self.display_name}, is_active={self.is_active})>"


class AdobeConnection(Base):
    """Adobe IMS connection (Analytics + Launch share this) scoped to a project."""

    __tablename__ = "adobe_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )  # Adobe Analytics company ID
    client_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Which Adobe products are enabled on this connection
    has_analytics: Mapped[bool] = mapped_column(Boolean, default=True)
    has_launch: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_status: Mapped[str] = mapped_column(String(50), default=CONNECTION_STATUS_ACTIVE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_adobe_project_user_active", project_id, user_id, is_active),)

    def __repr__(self) -> str:
        return f"<AdobeConnection(org_id={self.org_id}, is_active={self.is_active})>"


class MarketoConnection(Base):
    """Adobe Marketo Engage connection (own OAuth client-creds, not IMS) scoped to a project."""

    __tablename__ = "marketo_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Canonical REST endpoint base, e.g. https://123-ABC-456.mktorest.com (not secret).
    instance_url: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(50), default=CONNECTION_STATUS_ACTIVE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_marketo_project_user_active", project_id, user_id, is_active),)

    def __repr__(self) -> str:
        return f"<MarketoConnection(display_name={self.display_name}, is_active={self.is_active})>"


class RedshiftConnection(Base):
    """Amazon Redshift connection scoped to a project."""

    __tablename__ = "redshift_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    host_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=5439)
    database: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    username_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    default_schema: Mapped[str] = mapped_column(String(255), default="public")
    connection_status: Mapped[str] = mapped_column(String(50), default=CONNECTION_STATUS_ACTIVE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_redshift_project_user_active", project_id, user_id, is_active),)

    def __repr__(self) -> str:
        return f"<RedshiftConnection(database={self.database}, is_active={self.is_active})>"


class SnowflakeConnection(Base):
    """Snowflake connection scoped to a project."""

    __tablename__ = "snowflake_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    username_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    database: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    default_schema: Mapped[str] = mapped_column(String(255), default="PUBLIC")
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(50), default=CONNECTION_STATUS_ACTIVE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_snowflake_project_user_active", project_id, user_id, is_active),)

    def __repr__(self) -> str:
        return f"<SnowflakeConnection(warehouse={self.warehouse}, database={self.database}, is_active={self.is_active})>"
