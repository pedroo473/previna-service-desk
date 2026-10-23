"""Usuários, RBAC, equipes, filas e permissões por fila."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String,
    Table, UniqueConstraint, Uuid, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IDENTITY_PROVIDER_DB_ENUM, IdentityProvider, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .itsm import Macro, SavedFilter
    from .ticket import (
        AuditEvent, EmailOutbox, Notification, SatisfactionSurvey, Ticket,
        TicketAttachment, TicketComment, TicketFollower, TicketStatusHistory,
        TimeEntry,
    )


user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Uuid(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        UniqueConstraint("identity_provider", "external_subject", name="uq_users_external_identity"),
        CheckConstraint("username = lower(username)", name="ck_users_username_lowercase"),
        Index("ix_users_active_name", "is_active", "display_name"),
    )

    username: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    identity_provider: Mapped[IdentityProvider] = mapped_column(
        IDENTITY_PROVIDER_DB_ENUM, nullable=False,
        default=IdentityProvider.LOCAL,
    )
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, back_populates="users", lazy="selectin")
    team_memberships: Mapped[list[TeamMembership]] = relationship(back_populates="user", cascade="all, delete-orphan")
    queue_memberships: Mapped[list[QueueMembership]] = relationship(back_populates="user", cascade="all, delete-orphan")
    requested_tickets: Mapped[list[Ticket]] = relationship(
        "Ticket", foreign_keys="Ticket.requester_id", back_populates="requester"
    )
    assigned_tickets: Mapped[list[Ticket]] = relationship(
        "Ticket", foreign_keys="Ticket.assignee_id", back_populates="assignee"
    )
    comments: Mapped[list[TicketComment]] = relationship(back_populates="author")
    uploaded_attachments: Mapped[list[TicketAttachment]] = relationship(back_populates="uploaded_by")
    notifications: Mapped[list[Notification]] = relationship(back_populates="user", cascade="all, delete-orphan")
    followers: Mapped[list[TicketFollower]] = relationship(back_populates="user", cascade="all, delete-orphan")
    time_entries: Mapped[list[TimeEntry]] = relationship(back_populates="user")
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="actor")
    saved_filters: Mapped[list[SavedFilter]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    authored_macros: Mapped[list[Macro]] = relationship(back_populates="author")
    satisfaction_surveys: Mapped[list[SatisfactionSurvey]] = relationship(back_populates="requester")
    email_outbox_items: Mapped[list[EmailOutbox]] = relationship(back_populates="user")

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    def has_role(self, role_code: str) -> bool:
        return any(role.code == role_code for role in self.roles)

    def can_access_queue(self, queue_id: str, capability: str = "can_view") -> bool:
        """Verificação auxiliar; a autorização final deve ocorrer no serviço/query."""
        if self.has_role("admin"):
            return True
        return any(
            str(membership.queue_id) == str(queue_id) and getattr(membership, capability, False)
            for membership in self.queue_memberships
        )

    def __repr__(self) -> str:
        return f"<User username={self.username!r} id={self.id}>"


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("code", name="uq_roles_code"),)

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("code", name="uq_permissions_code"),)

    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    roles: Mapped[list[Role]] = relationship(secondary=role_permissions, back_populates="permissions")


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("code", name="uq_teams_code"),)

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    memberships: Mapped[list[TeamMembership]] = relationship(back_populates="team", cascade="all, delete-orphan")
    queues: Mapped[list[Queue]] = relationship(back_populates="team")


class TeamMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_memberships"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),)

    team_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_team_lead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    team: Mapped[Team] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="team_memberships")


class Queue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "queues"
    __table_args__ = (
        UniqueConstraint("code", name="uq_queues_code"),
        Index("ix_queues_active_name", "is_active", "name"),
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    team_id: Mapped["uuid.UUID | None"] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    team: Mapped[Team | None] = relationship(back_populates="queues")
    memberships: Mapped[list[QueueMembership]] = relationship(back_populates="queue", cascade="all, delete-orphan")
    tickets: Mapped[list[Ticket]] = relationship("Ticket", back_populates="queue")
    macros: Mapped[list[Macro]] = relationship("Macro", back_populates="queue")
    saved_filters: Mapped[list[SavedFilter]] = relationship("SavedFilter", back_populates="queue")


class QueueMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "queue_memberships"
    __table_args__ = (
        UniqueConstraint("queue_id", "user_id", name="uq_queue_memberships_queue_user"),
        CheckConstraint("can_assign = false OR can_view = true", name="ck_queue_assign_requires_view"),
        CheckConstraint("can_manage = false OR can_view = true", name="ck_queue_manage_requires_view"),
        CheckConstraint("can_view_internal_notes = false OR can_view = true", name="ck_queue_internal_notes_requires_view"),
    )

    queue_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped["uuid.UUID"] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    can_view: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    can_assign: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    can_manage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    can_view_internal_notes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    queue: Mapped[Queue] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="queue_memberships")
