"""Dependency-free smoke tests for the SQLite schema."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"


class SchemaIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp.close()
        self.db = sqlite3.connect(self.temp.name)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()
        Path(self.temp.name).unlink(missing_ok=True)

    def test_expected_tables_and_indexes_exist(self):
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"users", "tickets", "comments"}.issubset(tables))
        index_count = self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'").fetchone()[0]
        self.assertGreaterEqual(index_count, 4)

    def test_foreign_keys_allow_valid_ticket_flow(self):
        self.db.execute("INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES ('ti', 'TI', 'hash', 'ti', 1, '2026-10-22T10:00:00')")
        user_id = self.db.execute("SELECT id FROM users WHERE username='ti'").fetchone()[0]
        cur = self.db.execute("INSERT INTO tickets (title, description, category, priority, status, requester_id, created_at, updated_at) VALUES ('Teste', 'Descrição', 'Outro', 'Normal', 'Aberto', ?, '2026-10-22T10:00:00', '2026-10-22T10:00:00')", (user_id,))
        self.db.execute("INSERT INTO comments (ticket_id, user_id, body, created_at) VALUES (?, ?, 'Chamado aberto.', '2026-10-22T10:00:00')", (cur.lastrowid, user_id))
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM comments").fetchone()[0], 1)

    def test_invalid_role_and_status_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES ('x', 'X', 'hash', 'admin', 1, '2026-10-22T10:00:00')")
        self.db.execute("INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES ('ti', 'TI', 'hash', 'ti', 1, '2026-10-22T10:00:00')")
        user_id = self.db.execute("SELECT id FROM users WHERE username='ti'").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO tickets (title, description, category, priority, status, requester_id, created_at, updated_at) VALUES ('Teste', 'Descrição', 'Outro', 'Normal', 'Status inválido', ?, '2026-10-22T10:00:00', '2026-10-22T10:00:00')", (user_id,))

    def test_foreign_key_blocks_orphan_ticket(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO tickets (title, description, category, priority, status, requester_id, created_at, updated_at) VALUES ('Teste', 'Descrição', 'Outro', 'Normal', 'Aberto', 9999, '2026-10-22T10:00:00', '2026-10-22T10:00:00')")


if __name__ == "__main__":
    unittest.main(verbosity=2)
