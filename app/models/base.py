"""Tipos, enums e classes-base dos modelos ORM do Previna Service Desk."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, JSON, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarativa única registrada pelo Alembic."""


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now()
    )


# JSONB no PostgreSQL; JSON genérico mantém os modelos testáveis com SQLite.
JSONDocument = JSON().with_variant(JSONB, "postgresql")


def enum_type(enum_class: type[enum.Enum], name: str) -> SAEnum:
    """Persistir os valores declarados no enum, não seus nomes Python."""
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        native_enum=True,
        validate_strings=True,
    )


class IdentityProvider(str, enum.Enum):
    LOCAL = "local"
    ENTRA_ID = "entra_id"
    LDAP = "ldap"


class TicketStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_USER = "waiting_user"
    WAITING_THIRD_PARTY = "waiting_third_party"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELED = "canceled"


class TicketPriority(str, enum.Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class ImpactLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UrgencyLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommentVisibility(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"


class HolidayScope(str, enum.Enum):
    NATIONAL = "national"
    STATE = "state"
    MUNICIPAL = "municipal"
    COMPANY = "company"


class ArticleStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ArticleVisibility(str, enum.Enum):
    REQUESTERS = "requesters"
    STAFF = "staff"


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"


class NotificationType(str, enum.Enum):
    TICKET_CREATED = "ticket_created"
    TICKET_ASSIGNED = "ticket_assigned"
    TICKET_COMMENTED = "ticket_commented"
    TICKET_STATUS_CHANGED = "ticket_status_changed"
    TICKET_RESOLVED = "ticket_resolved"
    SLA_WARNING = "sla_warning"
    SLA_BREACHED = "sla_breached"
    SYSTEM = "system"

# Reutilizar as mesmas instâncias de ENUM evita declarar tipos PostgreSQL duplicados.
IDENTITY_PROVIDER_DB_ENUM = enum_type(IdentityProvider, "identity_provider")
TICKET_STATUS_DB_ENUM = enum_type(TicketStatus, "ticket_status")
TICKET_PRIORITY_DB_ENUM = enum_type(TicketPriority, "ticket_priority")
IMPACT_LEVEL_DB_ENUM = enum_type(ImpactLevel, "impact_level")
URGENCY_LEVEL_DB_ENUM = enum_type(UrgencyLevel, "urgency_level")
COMMENT_VISIBILITY_DB_ENUM = enum_type(CommentVisibility, "comment_visibility")
HOLIDAY_SCOPE_DB_ENUM = enum_type(HolidayScope, "holiday_scope")
ARTICLE_STATUS_DB_ENUM = enum_type(ArticleStatus, "article_status")
ARTICLE_VISIBILITY_DB_ENUM = enum_type(ArticleVisibility, "article_visibility")
OUTBOX_STATUS_DB_ENUM = enum_type(OutboxStatus, "outbox_status")
NOTIFICATION_TYPE_DB_ENUM = enum_type(NotificationType, "notification_type")

