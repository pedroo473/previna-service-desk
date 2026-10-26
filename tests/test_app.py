from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

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
        self.assertEqual(payload["unread_count"], 1)
        self.assertEqual(len(payload["latest"]), 1)
        self.assertIn("Novo chamado", payload["latest"][0]["title"])
        self.assertIn("/notificacoes/", payload["latest"][0]["open_url"])

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

if __name__ == "__main__":
    unittest.main(verbosity=2)
