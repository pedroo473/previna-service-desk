"""Smoke tests da metadata da Etapa 1; executa com python -m unittest."""
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import configure_mappers

from app.models import Base, Ticket, TicketStatus, TicketPriority, User


class ModelMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_mappers()
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_expected_tables_are_registered(self):
        tables = set(inspect(self.engine).get_table_names())
        expected = {
            "users", "roles", "permissions", "user_roles", "role_permissions",
            "teams", "team_memberships", "queues", "queue_memberships", "tickets",
            "ticket_comments", "ticket_attachments", "ticket_status_history", "ticket_slas",
            "ticket_counters", "business_calendars", "business_hours", "holidays",
            "sla_policies", "sla_targets", "categories", "category_fields", "audit_events",
            "notifications", "email_outbox", "knowledge_articles", "knowledge_feedback",
            "macros", "saved_filters", "time_entries", "ticket_followers", "satisfaction_surveys",
        }
        self.assertTrue(expected.issubset(tables))
        self.assertEqual(len(Base.metadata.tables), 32)

    def test_enum_values_are_explicit(self):
        self.assertEqual(TicketStatus.WAITING_USER.value, "waiting_user")
        self.assertEqual(TicketPriority.P1.value, "P1")

    def test_user_model_supports_flask_login_protocol(self):
        user = User(
            username="ti.teste", display_name="TI Teste", email="ti@example.invalid",
            email_normalized="ti@example.invalid", password_hash=None,
        )
        self.assertTrue(user.is_authenticated)
        self.assertFalse(user.is_anonymous)
        self.assertTrue(callable(user.get_id))

    def test_ticket_table_has_access_and_sla_indexes(self):
        indexes = {index.name for index in Ticket.__table__.indexes}
        self.assertIn("ix_tickets_requester_created", indexes)
        self.assertIn("ix_tickets_queue_status_created", indexes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
