"""Calendários, SLAs, categorias, conhecimento, macros e filtros salvos."""
from __future__ import annotations

import uuid
from datetime import date, time, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKey,
    Index, Integer, JSON, String, Text, Time, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    ARTICLE_STATUS_DB_ENUM, ARTICLE_VISIBILITY_DB_ENUM, ArticleStatus, ArticleVisibility, Base,
    HOLIDAY_SCOPE_DB_ENUM, HolidayScope, JSONDocument, TICKET_PRIORITY_DB_ENUM,
    TicketPriority, TimestampMixin, UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from .identity import Queue, User
    from .ticket import Ticket, TicketSLA


class BusinessCalendar(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_calendars"
    __table_args__ = (UniqueConstraint("code", name="uq_business_calendars_code"),)

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Sao_Paulo")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    business_hours: Mapped[list[BusinessHour]] = relationship(back_populates="calendar", cascade="all, delete-orphan")
    holidays: Mapped[list[Holiday]] = relationship(back_populates="calendar", cascade="all, delete-orphan")
    sla_policies: Mapped[list[SLAPolicy]] = relationship(back_populates="calendar")


class BusinessHour(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint("calendar_id", "weekday", "window_order", name="uq_business_hours_calendar_day_window"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_business_hours_weekday_range"),
        CheckConstraint("window_order >= 1", name="ck_business_hours_window_order_positive"),
        CheckConstraint("starts_at < ends_at", name="ck_business_hours_start_before_end"),
        Index("ix_business_hours_calendar_weekday", "calendar_id", "weekday"),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_calendars.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday=0 ... Sunday=6
    window_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)

    calendar: Mapped[BusinessCalendar] = relationship(back_populates="business_hours")


class Holiday(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "holidays"
    __table_args__ = (
        Index("ix_holidays_calendar_date", "calendar_id", "holiday_date"),
        CheckConstraint(
            "(scope <> 'state' OR region_code IS NOT NULL) AND "
            "(scope <> 'municipal' OR (region_code IS NOT NULL AND city_name IS NOT NULL))",
            name="ck_holidays_local_scope_has_region",
        ),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_calendars.id", ondelete="CASCADE"), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[HolidayScope] = mapped_column(HOLIDAY_SCOPE_DB_ENUM, nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    repeats_annually: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    calendar: Mapped[BusinessCalendar] = relationship(back_populates="holidays")


class SLAPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("code", name="uq_sla_policies_code"),
        CheckConstraint("effective_until IS NULL OR effective_until >= effective_from", name="ck_sla_policy_effective_range"),
        Index("ix_sla_policies_active", "is_active", "effective_from"),
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_calendars.id", ondelete="RESTRICT"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    calendar: Mapped[BusinessCalendar] = relationship(back_populates="sla_policies")
    targets: Mapped[list[SLATarget]] = relationship(back_populates="policy", cascade="all, delete-orphan")
    categories: Mapped[list[Category]] = relationship(back_populates="default_sla_policy")


class SLATarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sla_targets"
    __table_args__ = (
        UniqueConstraint("sla_policy_id", "priority", name="uq_sla_target_policy_priority"),
        CheckConstraint("first_response_minutes > 0", name="ck_sla_first_response_positive"),
        CheckConstraint("resolution_minutes > 0", name="ck_sla_resolution_positive"),
    )

    sla_policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sla_policies.id", ondelete="CASCADE"), nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(TICKET_PRIORITY_DB_ENUM, nullable=False)
    first_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    policy: Mapped[SLAPolicy] = relationship(back_populates="targets")
    ticket_slas: Mapped[list[TicketSLA]] = relationship("TicketSLA", back_populates="target")


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("parent_id", "code", name="uq_category_parent_code"),
        Index("ix_categories_active_name", "is_active", "name"),
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    default_sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sla_policies.id", ondelete="RESTRICT"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    parent: Mapped[Category | None] = relationship(remote_side="Category.id", back_populates="children")
    children: Mapped[list[Category]] = relationship(back_populates="parent")
    default_sla_policy: Mapped[SLAPolicy | None] = relationship(back_populates="categories")
    form_fields: Mapped[list[CategoryField]] = relationship(back_populates="category", cascade="all, delete-orphan")
    tickets: Mapped[list[Ticket]] = relationship("Ticket", foreign_keys="Ticket.category_id", back_populates="category")
    subcategory_tickets: Mapped[list[Ticket]] = relationship("Ticket", foreign_keys="Ticket.subcategory_id", back_populates="subcategory")
    knowledge_articles: Mapped[list[KnowledgeArticle]] = relationship(back_populates="category")


class CategoryField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "category_fields"
    __table_args__ = (
        UniqueConstraint("category_id", "field_key", name="uq_category_fields_category_key"),
        CheckConstraint("field_type IN ('text','textarea','number','date','boolean','select','multiselect')", name="ck_category_field_type"),
        Index("ix_category_fields_category_order", "category_id", "sort_order"),
    )

    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    options: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONDocument, nullable=True)
    validation_rules: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    category: Mapped[Category] = relationship(back_populates="form_fields")


class KnowledgeArticle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_articles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_knowledge_articles_slug"),
        Index("ix_knowledge_articles_state_visibility", "status", "visibility"),
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False, default=list)
    status: Mapped[ArticleStatus] = mapped_column(ARTICLE_STATUS_DB_ENUM, nullable=False, default=ArticleStatus.DRAFT)
    visibility: Mapped[ArticleVisibility] = mapped_column(ARTICLE_VISIBILITY_DB_ENUM, nullable=False, default=ArticleVisibility.REQUESTERS)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    category: Mapped[Category | None] = relationship(back_populates="knowledge_articles")
    author: Mapped[User] = relationship("User", foreign_keys=[author_id])
    published_by: Mapped[User | None] = relationship("User", foreign_keys=[published_by_id])
    feedback: Mapped[list[KnowledgeFeedback]] = relationship(back_populates="article", cascade="all, delete-orphan")


class KnowledgeFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_feedback"
    __table_args__ = (UniqueConstraint("article_id", "user_id", name="uq_knowledge_feedback_article_user"),)

    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    was_helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    article: Mapped[KnowledgeArticle] = relationship(back_populates="feedback")
    user: Mapped[User] = relationship("User")


class Macro(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "macros"
    __table_args__ = (UniqueConstraint("name", "queue_id", name="uq_macros_name_queue"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status_after: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    queue: Mapped[Queue | None] = relationship("Queue", back_populates="macros")
    author: Mapped[User] = relationship("User", back_populates="authored_macros")


class SavedFilter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "saved_filters"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_saved_filters_owner_name"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False, default=dict)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=True)

    owner: Mapped[User] = relationship("User", back_populates="saved_filters")
    queue: Mapped[Queue | None] = relationship("Queue", back_populates="saved_filters")
