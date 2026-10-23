"""Import central para registrar todos os modelos em Base.metadata/Alembic."""
from .base import (
    ArticleStatus, ArticleVisibility, Base, CommentVisibility, HolidayScope,
    IdentityProvider, ImpactLevel, NotificationType, OutboxStatus,
    TicketPriority, TicketStatus, UrgencyLevel,
)
from .identity import (
    Permission, Queue, QueueMembership, Role, Team, TeamMembership, User,
    role_permissions, user_roles,
)
from .itsm import (
    BusinessCalendar, BusinessHour, Category, CategoryField, Holiday, Macro,
    KnowledgeArticle, KnowledgeFeedback, SavedFilter, SLAPolicy, SLATarget,
)
from .ticket import (
    AuditEvent, EmailOutbox, Notification, SatisfactionSurvey, Ticket,
    TicketAttachment, TicketComment, TicketCounter, TicketFollower, TicketSLA,
    TicketStatusHistory, TimeEntry,
)

__all__ = [
    "ArticleStatus", "ArticleVisibility", "AuditEvent", "Base",
    "BusinessCalendar", "BusinessHour", "Category", "CategoryField",
    "CommentVisibility", "EmailOutbox", "Holiday", "HolidayScope",
    "IdentityProvider", "ImpactLevel", "KnowledgeArticle", "KnowledgeFeedback",
    "Macro", "Notification", "NotificationType", "OutboxStatus", "Permission",
    "Queue", "QueueMembership", "Role", "SavedFilter", "SatisfactionSurvey",
    "SLAPolicy", "SLATarget", "Team", "TeamMembership", "Ticket",
    "TicketAttachment", "TicketComment", "TicketCounter", "TicketFollower",
    "TicketPriority", "TicketSLA", "TicketStatus", "TicketStatusHistory",
    "TimeEntry", "UrgencyLevel", "User", "role_permissions", "user_roles",
]
