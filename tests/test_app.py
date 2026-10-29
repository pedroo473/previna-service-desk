from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app


class PrevinaTicketsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "DATABASE": self.db_path,
            "SECRET_KEY": "test-secret-key",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            from app import get_db, now_text
            db = get_db()
            db.execute(
                "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                ("solicitante", "Pessoa Solicitante", generate_password_hash("SenhaSegura123"), "solicitante", now_text()),
            )
            db.execute(
                "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                ("tecnico", "Técnico TI", generate_password_hash("SenhaSegura456"), "ti", now_text()),
            )
            db.execute(
                "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                ("outra.pessoa", "Outra Pessoa", generate_password_hash("SenhaSegura789"), "solicitante", now_text()),
            )
            db.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def login_as(self, username: str, password: str) -> None:
        response = self.client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def open_ticket(self, title: str = "Computador não conecta") -> int:
        response = self.client.post("/chamados/novo", data={
            "title": title,
            "description": "A estação não conecta à rede corporativa.",
            "category": "Rede e internet",
            "priority": "Alta",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            from app import get_db
            row = get_db().execute("SELECT id FROM tickets WHERE title = ?", (title,)).fetchone()
            self.assertIsNotNone(row)
            return int(row["id"])

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_login_rejects_wrong_password(self) -> None:
        response = self.client.post("/login", data={"username": "solicitante", "password": "errada"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Usuário ou senha inválidos".encode(), response.data)

    def test_requester_can_create_and_view_own_ticket(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket()
        response = self.client.get(f"/chamados/{ticket_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Computador n\xc3\xa3o conecta", response.data)

    def test_requester_cannot_view_another_users_ticket(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket("Chamado privado")
        self.client.post("/logout", follow_redirects=True)
        self.login_as("outra.pessoa", "SenhaSegura789")
        response = self.client.get(f"/chamados/{ticket_id}")
        self.assertEqual(response.status_code, 403)

    def test_only_ti_can_change_status(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket("Chamado para alterar status")
        denied = self.client.post(f"/chamados/{ticket_id}", data={"update_status": "1", "status": "Resolvido"})
        self.assertEqual(denied.status_code, 403)
        self.client.post("/logout", follow_redirects=True)
        self.login_as("tecnico", "SenhaSegura456")
        updated = self.client.post(f"/chamados/{ticket_id}", data={"update_status": "1", "status": "Em andamento"}, follow_redirects=True)
        self.assertEqual(updated.status_code, 200)
        with self.app.app_context():
            from app import get_db
            row = get_db().execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual(row["status"], "Em andamento")

    def test_comments_are_saved(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket("Chamado com comentário")
        response = self.client.post(f"/chamados/{ticket_id}", data={"body": "O erro ocorre desde hoje cedo."}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"O erro ocorre desde hoje cedo.", response.data)



    def test_requester_cannot_delete_ticket(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket("Chamado sem permissão para excluir")
        response = self.client.post(f"/chamados/{ticket_id}/excluir", follow_redirects=True)
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            from app import get_db
            ticket = get_db().execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            self.assertIsNotNone(ticket)

    def test_ti_can_delete_ticket_and_keep_audit_record(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        ticket_id = self.open_ticket("Chamado para excluir")
        self.client.post("/logout", follow_redirects=True)
        self.login_as("tecnico", "SenhaSegura456")

        response = self.client.post(f"/chamados/{ticket_id}/excluir", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"Chamado #{ticket_id} excluído com sucesso.".encode(), response.data)

        with self.app.app_context():
            from app import get_db
            db = get_db()
            ticket = db.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            self.assertIsNone(ticket)
            comments = db.execute("SELECT COUNT(*) amount FROM comments WHERE ticket_id=?", (ticket_id,)).fetchone()
            self.assertEqual(int(comments["amount"]), 0)
            notice = db.execute(
                """SELECT title,target_url FROM notifications
                   WHERE title='Chamado removido' AND target_url=? LIMIT 1""",
                ("/notificacoes",),
            ).fetchone()
            self.assertIsNotNone(notice)
            audit = db.execute(
                """SELECT ticket_id,description FROM audit_logs
                   WHERE event_type='ticket_deleted' AND description LIKE ? ORDER BY id DESC LIMIT 1""",
                (f"%#{ticket_id}%",),
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertIsNone(audit["ticket_id"])

    def test_login_rate_limit_blocks_repeated_failures(self) -> None:
        for _ in range(5):
            response = self.client.post(
                "/login", data={"username": "solicitante", "password": "senha-incorreta"},
            )
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post(
            "/login", data={"username": "solicitante", "password": "SenhaSegura123"},
        )
        self.assertEqual(blocked.status_code, 429)

    def test_login_rejects_external_or_ambiguous_next_redirect(self) -> None:
        for target in ("https://evil.invalid", "//evil.invalid", "///evil.invalid", r"/\\evil.invalid", "/%2f%2fevil.invalid"):
            with self.subTest(target=target):
                client = self.app.test_client()
                response = client.post(
                    "/login",
                    data={"username": "solicitante", "password": "SenhaSegura123", "next": target},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers.get("Location"), "/")

    def test_login_allows_safe_local_next_redirect(self) -> None:
        response = self.client.post(
            "/login",
            data={"username": "solicitante", "password": "SenhaSegura123", "next": "/?period=7d"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/?period=7d")

    def test_csrf_is_enforced_when_enabled(self) -> None:
        temp_db = str(Path(self.temp_dir.name) / "csrf.sqlite3")
        csrf_app = create_app({
            "TESTING": True,
            "DATABASE": temp_db,
            "SECRET_KEY": "csrf-test-secret",
            "WTF_CSRF_ENABLED": True,
        })
        response = csrf_app.test_client().post("/login", data={"username": "x", "password": "y"})
        self.assertEqual(response.status_code, 400)

    def test_production_requires_explicit_secret_key(self) -> None:
        with patch.dict(os.environ, {"PREVINA_ENV": "production", "FLASK_SECRET_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                create_app()

    def test_production_enables_secure_cookie_by_default(self) -> None:
        temp_db = str(Path(self.temp_dir.name) / "production.sqlite3")
        with patch.dict(os.environ, {"PREVINA_ENV": "production", "FLASK_SECRET_KEY": "stable-production-test-secret"}):
            production_app = create_app({
                "TESTING": True, "DATABASE": temp_db, "SECRET_KEY": "test-secret",
            })
        self.assertTrue(production_app.config["SESSION_COOKIE_SECURE"])

    def test_timestamps_are_stored_as_utc_and_displayed_in_sao_paulo(self) -> None:
        from app import now_text, parse_timestamp, timestamp_epoch
        generated = parse_timestamp(now_text())
        self.assertIsNotNone(generated)
        self.assertEqual(generated.utcoffset(), timezone.utc.utcoffset(generated))
        self.assertAlmostEqual(
            timestamp_epoch("2026-10-29T15:00:00+00:00"),
            timestamp_epoch("2026-10-29T12:00:00"),
            places=5,
        )
        with self.app.app_context():
            formatter = self.app.jinja_env.filters["datetime_br"]
            self.assertEqual(formatter("2026-10-29T15:00:00+00:00"), "29/10/2026 às 12:00")
            self.assertEqual(formatter("2026-10-29T12:00:00"), "29/10/2026 às 12:00")

    def test_dashboard_paginates_and_preserves_full_filtered_count(self) -> None:
        with self.app.app_context():
            from app import get_db, now_text
            db = get_db()
            requester = db.execute(
                "SELECT id FROM users WHERE username=?", ("solicitante",)
            ).fetchone()
            timestamp = now_text()
            for number in range(30):
                db.execute(
                    """INSERT INTO tickets
                       (title,description,category,priority,status,requester_id,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (f"Ticket Pagination {number:02d}", "Descrição de teste", "Outro",
                     "Normal", "Aberto", requester["id"], timestamp, timestamp),
                )
            db.commit()
        self.login_as("tecnico", "SenhaSegura456")
        first = self.client.get("/?q=Ticket%20Pagination&period=all")
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"30</strong> chamados na lista", first.data)
        self.assertIn("Página 1 de 2".encode(), first.data)
        self.assertIn("Próxima".encode(), first.data)
        second = self.client.get("/?q=Ticket%20Pagination&period=all&page=2")
        self.assertEqual(second.status_code, 200)
        self.assertIn("Página 2 de 2".encode(), second.data)
        self.assertIn(b"Ticket Pagination 04", second.data)
        self.assertNotIn(b"Ticket Pagination 29", second.data)

    def test_dashboard_metrics_are_mutually_exclusive(self) -> None:
        with self.app.app_context():
            from app import get_db, now_text
            db = get_db()
            requester = db.execute("SELECT id FROM users WHERE username = ?", ("solicitante",)).fetchone()
            timestamp = now_text()
            examples = (
                ("Chamado em andamento", "Descrição.", "Rede e internet", "Normal", "Em andamento"),
                ("Chamado encerrado", "Descrição.", "Rede e internet", "Normal", "Encerrado"),
            )
            for title, description, category, priority, status in examples:
                db.execute(
                    """INSERT INTO tickets
                       (title,description,category,priority,status,requester_id,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (title, description, category, priority, status, requester["id"], timestamp, timestamp),
                )
            db.commit()

        self.login_as("tecnico", "SenhaSegura456")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        metrics = {
            name.decode(): int(value)
            for name, value in re.findall(rb'data-metric="([^"]+)">\s*(\d+)\s*</strong>', response.data)
        }
        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["open_count"], 0)
        self.assertEqual(metrics["in_progress_count"], 1)
        self.assertEqual(metrics["waiting_count"], 0)
        self.assertEqual(metrics["resolved_closed_count"], 1)
        self.assertEqual(
            metrics["open_count"] + metrics["in_progress_count"] +
            metrics["waiting_count"] + metrics["resolved_closed_count"],
            metrics["total"],
        )

    def test_period_applies_to_metrics_and_list(self) -> None:
        with self.app.app_context():
            from app import get_db, now_text
            db = get_db()
            requester = db.execute("SELECT id FROM users WHERE username = ?", ("solicitante",)).fetchone()
            recent = now_text()
            old = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
            examples = (
                ("Chamado recente do período", "Descrição.", "Rede e internet", "Normal", "Aberto", recent),
                ("Chamado antigo fora do período", "Descrição.", "Rede e internet", "Normal", "Aberto", old),
            )
            for title, description, category, priority, status, created in examples:
                db.execute(
                    """INSERT INTO tickets
                       (title,description,category,priority,status,requester_id,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (title, description, category, priority, status, requester["id"], created, created),
                )
            db.commit()

        self.login_as("tecnico", "SenhaSegura456")
        response = self.client.get("/?period=7d")
        self.assertEqual(response.status_code, 200)
        metrics = {
            name.decode(): int(value)
            for name, value in re.findall(rb'data-metric="([^"]+)">\s*(\d+)\s*</strong>', response.data)
        }
        self.assertEqual(metrics["total"], 1)
        self.assertIn("Chamado recente do período".encode(), response.data)
        self.assertNotIn("Chamado antigo fora do período".encode(), response.data)

    def test_new_ticket_creates_notification_for_technician(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        self.open_ticket("Chamado com notificação")
        self.client.post("/logout", follow_redirects=True)
        self.login_as("tecnico", "SenhaSegura456")
        response = self.client.get("/api/notificacoes/resumo")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # Login/logout events also produce notifications; assert the ticket event
        # specifically instead of assuming it is the only unread item.
        self.assertGreaterEqual(payload["unread_count"], 1)
        ticket_notice = next(
            (item for item in payload["latest"] if "Novo chamado" in item["title"]),
            None,
        )
        self.assertIsNotNone(ticket_notice)
        self.assertIn("/notificacoes/", ticket_notice["open_url"])

    def test_notification_cannot_be_opened_by_another_user(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        self.open_ticket("Chamado com notificação privada")
        self.client.post("/logout", follow_redirects=True)
        self.login_as("tecnico", "SenhaSegura456")
        payload = self.client.get("/api/notificacoes/resumo").get_json()
        notification_id = payload["latest"][0]["id"]
        self.client.post("/logout", follow_redirects=True)
        self.login_as("outra.pessoa", "SenhaSegura789")
        response = self.client.get(f"/notificacoes/{notification_id}/abrir")
        self.assertEqual(response.status_code, 404)

    def test_notifications_page_and_summary_are_available_after_login(self) -> None:
        self.login_as("tecnico", "SenhaSegura456")
        self.assertEqual(self.client.get("/notificacoes").status_code, 200)
        self.assertEqual(self.client.get("/api/notificacoes/resumo").status_code, 200)


    def test_demo_seed_is_blocked_when_demo_mode_is_disabled(self) -> None:
        result = self.app.test_cli_runner().invoke(args=["seed-demo-data"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("DEMO_MODE=true", result.output)


    def test_profile_updates_name_and_email(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        response = self.client.post(
            "/perfil",
            data={"full_name": "Solicitante Atualizado", "email": "solicitante@example.com"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Perfil atualizado com sucesso".encode(), response.data)
        with self.app.app_context():
            from app import get_db
            row = get_db().execute(
                "SELECT full_name,email FROM users WHERE username=?",
                ("solicitante",),
            ).fetchone()
            self.assertEqual(row["full_name"], "Solicitante Atualizado")
            self.assertEqual(row["email"], "solicitante@example.com")

    def test_audit_log_is_restricted_to_ti(self) -> None:
        self.login_as("solicitante", "SenhaSegura123")
        denied = self.client.get("/auditoria")
        self.assertEqual(denied.status_code, 403)
        self.client.post("/logout", follow_redirects=True)
        self.login_as("tecnico", "SenhaSegura456")
        allowed = self.client.get("/auditoria")
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("Auditoria".encode(), allowed.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
