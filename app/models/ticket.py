"""Chamados, timeline, anexos, SLA por chamado, auditoria e notificações."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, Uuid, event, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base, COMMENT_VISIBILITY_DB_ENUM, IMPACT_LEVEL_DB_ENUM, JSONDocument,
    NOTIFICATION_TYPE_DB_ENUM, NotificationType, OUTBOX_STATUS_DB_ENUM, OutboxStatus,
    TICKET_PRIORITY_DB_ENUM, TICKET_STATUS_DB_ENUM, URGENCY_LEVEL_DB_ENUM,
    CommentVisibility, ImpactLevel, TicketPriority, TicketStatus, UrgencyLevel,
    TimestampMixin, UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from .identity import Queue, User
    from .itsm import Category, SLATarget


class TicketCounter(Base):
    """Contador anual; incremento concorrente deve ser feito por UPSERT atômico no PostgreSQL."""
    __tablename__ = "ticket_counters"
    __table_args__ = (
        CheckConstraint("year >= 2000 AND year <= 9999", name="ck_ticket_counter_year_range"),
        CheckConstraint("last_number >= 0", name="ck_ticket_counter_nonnegative"),
    )

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("ticket_number", name="uq_tickets_ticket_number"),
        CheckConstraint("length(ticket_number) = 14", name="ck_ticket_number_length"),
        CheckConstraint("length(trim(title)) > 0", name="ck_ticket_title_nonempty"),
        CheckConstraint("reopened_count >= 0", name="ck_ticket_reopened_count_nonnegative"),
        Index("ix_tickets_queue_status_created", "queue_id", "status", "created_at"),
        Index("ix_tickets_requester_created", "requester_id", "created_at"),
        Index("ix_tickets_assignee_status", "assignee_id", "status"),
        Index("ix_tickets_status_priority_updated", "status", "priority", "updated_at"),
    )

    ticket_number: Mapped[str] = mapped_column(String(15), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(TICKET_STATUS_DB_ENUM, nullable=False, default=TicketStatus.NEW)
    priority: Mapped[TicketPriority] = mapped_column(TICKET_PRIORITY_DB_ENUM, nullable=False, default=TicketPriority.P3)
    impact: Mapped[ImpactLevel] = mapped_column(IMPACT_LEVEL_DB_ENUM, nullable=False, default=ImpactLevel.MEDIUM)
    urgency: Mapped[UrgencyLevel] = mapped_column(URGENCY_LEVEL_DB_ENUM, nullable=False, default=UrgencyLevel.MEDIUM)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("queues.id", ondelete="RESTRICT"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False, default=dict)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    merged_into_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)

    requester: Mapped[User] = relationship("User", foreign_keys=[requester_id], back_populates="requested_tickets")
    assignee: Mapped[User | None] = relationship("User", foreign_keys=[assignee_id], back_populates="assigned_tickets")
    queue: Mapped[Queue] = relationship("Queue", back_populates="tickets")
    category: Mapped[Category] = relationship("Category", foreign_keys=[category_id], back_populates="tickets")
    subcategory: Mapped[Category | None] = relationship("Category", foreign_keys=[subcategory_id], back_populates="subcategory_tickets")
    merged_into: Mapped[Ticket | None] = relationship(remote_side="Ticket.id", foreign_keys=[merged_into_ticket_id])
    comments: Mapped[list[TicketComment]] = relationship(back_populates="ticket", cascade="all, delete-orphan", order_by="TicketComment.created_at")
    attachments: Mapped[list[TicketAttachment]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    status_history: Mapped[list[TicketStatusHistory]] = relationship(back_populates="ticket", cascade="all, delete-orphan", order_by="TicketStatusHistory.changed_at")
    sla: Mapped[TicketSLA | None] = relationship(back_populates="ticket", cascade="all, delete-orphan", uselist=False)
    followers: Mapped[list[TicketFollower]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    time_entries: Mapped[list[TimeEntry]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    notifications: Mapped[list[Notification]] = relationship(back_populates="ticket")
    email_outbox_items: Mapped[list[EmailOutbox]] = relationship(back_populates="ticket")
    satisfaction_surveys: Mapped[list[SatisfactionSurvey]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ticket_comments"
    __table_args__ = (
        CheckConstraint("length(trim(body_markdown)) > 0", name="ck_ticket_comment_nonempty"),
        Index("ix_ticket_comments_ticket_created", "ticket_id", "created_at"),
        Index("ix_ticket_comments_visibility_created", "ticket_id", "visibility", "created_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    visibility: Mapped[CommentVisibility] = mapped_column(COMMENT_VISIBILITY_DB_ENUM, nullable=False, default=CommentVisibility.PUBLIC)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    is_system_message: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    in_reply_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket_comments.id", ondelete="SET NULL"), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
    author: Mapped[User] = relationship("User", back_populates="comments")
    in_reply_to: Mapped[TicketComment | None] = relationship(remote_side="TicketComment.id", foreign_keys=[in_reply_to_id])
    attachments: Mapped[list[TicketAttachment]] = relationship(back_populates="comment")


class TicketAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ticket_attachments"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_ticket_attachment_positive_size"),
        CheckConstraint("length(sha256) = 64", name="ck_ticket_attachment_sha256_length"),
        Index("ix_ticket_attachments_ticket_created", "ticket_id", "created_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket_comments.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    extension: Mapped[str] = mapped_column(String(12), nullable=False)
    detected_mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="attachments")
    comment: Mapped[TicketComment | None] = relationship(back_populates="attachments")
    uploaded_by: Mapped[User] = relationship("User", back_populates="uploaded_attachments")


class TicketStatusHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ticket_status_history"
    __table_args__ = (
        Index("ix_ticket_status_history_ticket_changed", "ticket_id", "changed_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[TicketStatus | None] = mapped_column(TICKET_STATUS_DB_ENUM, nullable=True)
    to_status: Mapped[TicketStatus] = mapped_column(TICKET_STATUS_DB_ENUM, nullable=False)
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="status_history")
    changed_by: Mapped[User | None] = relationship("User")


class TicketSLA(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ticket_slas"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_slas_ticket"),
        CheckConstraint("first_response_target_minutes > 0", name="ck_ticket_sla_first_target_positive"),
        CheckConstraint("resolution_target_minutes > 0", name="ck_ticket_sla_resolution_target_positive"),
        CheckConstraint("paused_seconds >= 0", name="ck_ticket_sla_paused_nonnegative"),
        Index("ix_ticket_slas_first_due", "first_response_due_at"),
        Index("ix_ticket_slas_resolution_due", "resolution_due_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    sla_target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sla_targets.id", ondelete="RESTRICT"), nullable=False)
    # Snapshot: alteração futura da política não modifica a meta histórica deste chamado.
    first_response_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pause_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="sla")
    target: Mapped[SLATarget] = relationship("SLATarget", back_populates="ticket_slas")


class TicketFollower(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ticket_followers"
    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="uq_ticket_followers_ticket_user"),)

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receive_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    ticket: Mapped[Ticket] = relationship(back_populates="followers")
    user: Mapped[User] = relationship(back_populates="followers")


class TimeEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        CheckConstraint("minutes_spent > 0", name="ck_time_entries_minutes_positive"),
        Index("ix_time_entries_ticket_started", "ticket_id", "started_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    minutes_spent: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    ticket: Mapped[Ticket] = relationship(back_populates="time_entries")
    user: Mapped[User] = relationship("User", back_populates="time_entries")


class SatisfactionSurvey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "satisfaction_surveys"
    __table_args__ = (
        UniqueConstraint("ticket_id", "resolution_cycle", name="uq_satisfaction_ticket_cycle"),
        CheckConstraint("resolution_cycle >= 1", name="ck_satisfaction_cycle_positive"),
        CheckConstraint("score IS NULL OR (score >= 1 AND score <= 5)", name="ck_satisfaction_score_range"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    resolution_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="satisfaction_surveys")
    requester: Mapped[User] = relationship("User", back_populates="satisfaction_surveys")


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread_created", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    notification_type: Mapped[NotificationType] = mapped_column(NOTIFICATION_TYPE_DB_ENUM, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True, unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="notifications")
    ticket: Mapped[Ticket | None] = relationship(back_populates="notifications")


class EmailOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Outbox transacional; o worker envia mensagens e aplica retentativas idempotentes."""
    __tablename__ = "email_outbox"
    __table_args__ = (
        Index("ix_email_outbox_status_available", "status", "available_at"),
        CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts_nonnegative"),
    )

    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(OUTBOX_STATUS_DB_ENUM, nullable=False, default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)

    user: Mapped[User | None] = relationship("User", back_populates="email_outbox_items")
    ticket: Mapped[Ticket | None] = relationship(back_populates="email_outbox_items")


class AuditEvent(Base):
    """Evento append-only; trigger/permissões no PostgreSQL serão criados na etapa de migrations."""
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity_created", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument, nullable=True)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    actor: Mapped[User | None] = relationship("User", back_populates="audit_events")


def _audit_is_append_only(_mapper: Any, _connection: Any, _target: AuditEvent) -> None:
    raise ValueError("AuditEvent é append-only: atualização/exclusão não permitida pelo ORM.")


event.listen(AuditEvent, "before_update", _audit_is_append_only)
event.listen(AuditEvent, "before_delete", _audit_is_append_only)
