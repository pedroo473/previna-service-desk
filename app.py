from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import click
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

APP_TITLE = "Previna | Central de Chamados de TI"
ROLES = {"solicitante", "ti"}
STATUSES = ("Aberto", "Em andamento", "Aguardando usuário", "Resolvido", "Encerrado")
CLOSED_STATUSES = {"Resolvido", "Encerrado"}
PRIORITIES = ("Baixa", "Normal", "Alta", "Urgente")
CATEGORIES = (
    "Computadores e periféricos",
    "Sistemas e aplicativos",
    "Acesso e senhas",
    "Rede e internet",
    "Impressoras",
    "Telefonia",
    "E-mail e Microsoft 365",
    "Outro",
)


def now_text() -> str:
    """Timestamp in the server's local time, stored in sortable ISO format."""
    return datetime.now().isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = Path(str(current_app.config["DATABASE"]))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        g.db = connection
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    db = get_db()
    schema_path = Path(__file__).with_name("schema.sql")
    db.executescript(schema_path.read_text(encoding="utf-8"))
    db.commit()


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        # Keep local sessions valid across restarts without committing a secret to source control.
        secret_path = Path(app.instance_path) / ".secret_key"
        if not secret_path.exists():
            secret_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        secret_key = secret_path.read_text(encoding="utf-8").strip()

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        DATABASE=os.environ.get("PREVINA_DATABASE", str(Path(app.instance_path) / "chamados.sqlite3")),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,  # Enable when deployed behind HTTPS.
        WTF_CSRF_TIME_LIMIT=3600,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if test_config:
        app.config.update(test_config)

    csrf = CSRFProtect()
    csrf.init_app(app)

    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    @app.before_request
    def load_logged_in_user() -> None:
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = get_db().execute(
                "SELECT id, username, full_name, role, active FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if g.user is None or not g.user["active"]:
                session.clear()
                g.user = None

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "app_title": APP_TITLE,
            "categories": CATEGORIES,
            "priorities": PRIORITIES,
            "statuses": STATUSES,
            "current_user": getattr(g, "user", None),
        }

    @app.template_filter("datetime_br")
    def datetime_br(value: str | None) -> str:
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(value).strftime("%d/%m/%Y às %H:%M")
        except (TypeError, ValueError):
            return str(value)

    def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped_view(*args: Any, **kwargs: Any) -> Any:
            if getattr(g, "user", None) is None:
                flash("Entre com seu usuário para continuar.", "warning")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped_view

    def technician_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped_view(*args: Any, **kwargs: Any) -> Any:
            if g.user["role"] != "ti":
                abort(403)
            return view(*args, **kwargs)
        return wrapped_view

    def get_visible_ticket(ticket_id: int) -> sqlite3.Row:
        db = get_db()
        ticket = db.execute(
            """SELECT t.*, u.full_name AS requester_name, u.username AS requester_username
               FROM tickets t JOIN users u ON u.id = t.requester_id WHERE t.id = ?""",
            (ticket_id,),
        ).fetchone()
        if ticket is None:
            abort(404)
        if g.user["role"] != "ti" and ticket["requester_id"] != g.user["id"]:
            abort(403)
        return ticket

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if getattr(g, "user", None) is not None:
            return redirect(url_for("index"))
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Usuário ou senha inválidos.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = True
                next_url = request.args.get("next", "") or request.form.get("next", "")
                # Only allow local relative redirects to avoid open redirects.
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        session.clear()
        flash("Sessão encerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/", methods=["GET"])
    @login_required
    def index() -> Any:
        db = get_db()
        status_filter = request.args.get("status", "").strip()
        search = request.args.get("q", "").strip()[:100]
        clauses: list[str] = []
        params: list[Any] = []
        if g.user["role"] != "ti":
            clauses.append("t.requester_id = ?")
            params.append(g.user["id"])
        if status_filter in STATUSES:
            clauses.append("t.status = ?")
            params.append(status_filter)
        if search:
            like = f"%{search}%"
            clauses.append("(CAST(t.id AS TEXT) LIKE ? OR t.title LIKE ? OR t.category LIKE ? OR u.full_name LIKE ?)")
            params.extend([like, like, like, like])
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        tickets = db.execute(
            f"""SELECT t.id, t.title, t.category, t.priority, t.status, t.created_at,
                       t.updated_at, u.full_name AS requester_name
                FROM tickets t JOIN users u ON u.id = t.requester_id
                {where_sql} ORDER BY
                  CASE WHEN t.status IN ('Resolvido', 'Encerrado') THEN 1 ELSE 0 END,
                  CASE t.priority WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 WHEN 'Normal' THEN 3 ELSE 4 END,
                  t.updated_at DESC, t.id DESC""",
            params,
        ).fetchall()

        count_clauses: list[str] = []
        count_params: list[Any] = []
        if g.user["role"] != "ti":
            count_clauses.append("requester_id = ?")
            count_params.append(g.user["id"])
        count_where = " WHERE " + " AND ".join(count_clauses) if count_clauses else ""
        counts = db.execute(
            f"""SELECT COUNT(*) AS total,
                SUM(CASE WHEN status NOT IN ('Resolvido', 'Encerrado') THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status = 'Em andamento' THEN 1 ELSE 0 END) AS in_progress_count,
                SUM(CASE WHEN status = 'Resolvido' THEN 1 ELSE 0 END) AS resolved_count
                FROM tickets{count_where}""",
            count_params,
        ).fetchone()
        summary = {key: int(counts[key] or 0) for key in ("total", "open_count", "in_progress_count", "resolved_count")}
        return render_template(
            "index.html", tickets=tickets, summary=summary,
            status_filter=status_filter, search=search,
        )

    @app.route("/chamados/novo", methods=["GET", "POST"])
    @login_required
    def new_ticket() -> Any:
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "")
            priority = request.form.get("priority", "Normal")
            errors: list[str] = []
            if not title or len(title) > 120:
                errors.append("Informe um título com até 120 caracteres.")
            if not description or len(description) > 4000:
                errors.append("Descreva o problema (até 4.000 caracteres).")
            if category not in CATEGORIES:
                errors.append("Selecione uma categoria válida.")
            if priority not in PRIORITIES:
                errors.append("Selecione uma prioridade válida.")
            if errors:
                for message in errors:
                    flash(message, "error")
                return render_template("new_ticket.html", form=request.form), 400

            timestamp = now_text()
            db = get_db()
            cursor = db.execute(
                """INSERT INTO tickets (title, description, category, priority, status, requester_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'Aberto', ?, ?, ?)""",
                (title, description, category, priority, g.user["id"], timestamp, timestamp),
            )
            ticket_id = cursor.lastrowid
            db.execute(
                "INSERT INTO comments (ticket_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                (ticket_id, g.user["id"], "Chamado aberto.", timestamp),
            )
            db.commit()
            flash(f"Chamado #{ticket_id} aberto com sucesso.", "success")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))
        return render_template("new_ticket.html", form={})

    @app.route("/chamados/<int:ticket_id>", methods=["GET", "POST"])
    @login_required
    def ticket_detail(ticket_id: int) -> Any:
        db = get_db()
        ticket = get_visible_ticket(ticket_id)
        if request.method == "POST":
            if "update_status" in request.form:
                if g.user["role"] != "ti":
                    abort(403)
                new_status = request.form.get("status", "")
                if new_status not in STATUSES:
                    flash("Status inválido.", "error")
                    return redirect(url_for("ticket_detail", ticket_id=ticket_id))
                if new_status != ticket["status"]:
                    timestamp = now_text()
                    db.execute(
                        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                        (new_status, timestamp, ticket_id),
                    )
                    db.execute(
                        "INSERT INTO comments (ticket_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                        (ticket_id, g.user["id"], f"Status alterado para: {new_status}.", timestamp),
                    )
                    db.commit()
                    flash("Status atualizado.", "success")
                else:
                    flash("O chamado já está nesse status.", "info")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))

            body = request.form.get("body", "").strip()
            if not body:
                flash("Digite uma mensagem antes de enviar.", "error")
            elif len(body) > 2000:
                flash("O comentário deve ter até 2.000 caracteres.", "error")
            else:
                timestamp = now_text()
                db.execute(
                    "INSERT INTO comments (ticket_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                    (ticket_id, g.user["id"], body, timestamp),
                )
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (timestamp, ticket_id))
                db.commit()
                flash("Comentário enviado.", "success")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))

        comments = db.execute(
            """SELECT c.id, c.body, c.created_at, u.full_name AS author_name, u.role AS author_role
               FROM comments c JOIN users u ON u.id = c.user_id
               WHERE c.ticket_id = ? ORDER BY c.id ASC""",
            (ticket_id,),
        ).fetchall()
        technicians = g.user["role"] == "ti"
        return render_template("ticket_detail.html", ticket=ticket, comments=comments, is_technician=technicians)

    @app.get("/health")
    def health() -> Any:
        # No sensitive data; useful for local smoke checks.
        get_db().execute("SELECT 1").fetchone()
        return {"app": "Previna Chamados", "status": "ok"}

    @app.errorhandler(403)
    def forbidden(_error: Any) -> Any:
        return render_template("error.html", code=403, message="Você não tem permissão para acessar este recurso."), 403

    @app.errorhandler(404)
    def not_found(_error: Any) -> Any:
        return render_template("error.html", code=404, message="O recurso solicitado não foi encontrado."), 404

    @app.errorhandler(413)
    def file_too_large(_error: Any) -> Any:
        return render_template("error.html", code=413, message="A solicitação excedeu o limite permitido."), 413

    @app.cli.command("create-user")
    @click.option("--username", prompt="Usuário de acesso")
    @click.option("--name", "full_name", prompt="Nome completo")
    @click.option("--role", type=click.Choice(["solicitante", "ti"], case_sensitive=False), prompt="Perfil (solicitante/ti)")
    def create_user_command(username: str, full_name: str, role: str) -> None:
        """Create a user. Password is requested interactively and stored as a hash."""
        username = username.strip().lower()
        full_name = full_name.strip()
        role = role.lower()
        if not username or len(username) > 50 or not full_name or len(full_name) > 100:
            raise click.ClickException("Usuário e nome são obrigatórios e devem respeitar os limites de tamanho.")
        password = click.prompt("Senha (mínimo 10 caracteres)", hide_input=True, confirmation_prompt=True)
        if len(password) < 10:
            raise click.ClickException("A senha deve ter pelo menos 10 caracteres.")
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise click.ClickException("Esse nome de usuário já existe.")
        db.execute(
            "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (username, full_name, generate_password_hash(password), role, now_text()),
        )
        db.commit()
        click.echo(f"Usuário '{username}' criado com perfil '{role}'.")

    @app.cli.command("list-users")
    def list_users_command() -> None:
        """List user accounts without exposing password hashes."""
        rows = get_db().execute(
            "SELECT id, username, full_name, role, active FROM users ORDER BY full_name"
        ).fetchall()
        if not rows:
            click.echo("Nenhum usuário cadastrado. Execute: flask --app app.py create-user")
            return
        for row in rows:
            click.echo(f"{row['id']} | {row['username']} | {row['full_name']} | {row['role']} | ativo={row['active']}")

    return app


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
