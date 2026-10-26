from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import click
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_TITLE = "Previna | Service Desk"
ROLES = {"solicitante", "ti"}
STATUSES = ("Aberto", "Em andamento", "Aguardando usuário", "Resolvido", "Encerrado")
PRIORITIES = ("Baixa", "Normal", "Alta", "Urgente")
CATEGORIES = (
    "Computadores e periféricos", "Sistemas e aplicativos", "Acesso e senhas",
    "Rede e internet", "Impressoras", "Telefonia", "E-mail e Microsoft 365", "Outro",
)
PERIOD_OPTIONS = (
    ("all", "Todo o período"), ("7d", "Últimos 7 dias"),
    ("30d", "Últimos 30 dias"), ("90d", "Últimos 90 dias"),
)
PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = Path(str(current_app_config_database()))
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        g.db = connection
    return g.db


def current_app_config_database() -> str:
    from flask import current_app
    return str(current_app.config["DATABASE"])


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    db = get_db()
    db.executescript((PROJECT_ROOT / "schema.sql").read_text(encoding="utf-8"))
    # Migração aditiva: mantém usuários e chamados já existentes.
    for table in ("users", "tickets"):
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if "is_demo" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC, id DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read)")
    db.commit()


def add_notification(db: sqlite3.Connection, user_id: int, ticket_id: int | None,
                     kind: str, title: str, message: str, target_url: str, created_at: str) -> None:
    db.execute(
        """INSERT INTO notifications(user_id,ticket_id,kind,title,message,target_url,is_read,created_at)
           VALUES(?,?,?,?,?,?,0,?)""",
        (user_id, ticket_id, kind, title[:120], message[:500], target_url, created_at),
    )


def backup_sqlite_database(database_path: Path) -> Path:
    if not database_path.exists():
        raise click.ClickException("Banco não encontrado; nenhuma informação foi alterada.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = database_path.with_name(f"{database_path.name}.bak_{stamp}")
    source = sqlite3.connect(str(database_path), timeout=10)
    destination = sqlite3.connect(str(target), timeout=10)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def ensure_demo_database_isolated(app: Flask) -> None:
    """Impede a rotina de demonstração de usar o banco padrão da aplicação."""
    explicit_database = os.environ.get("PREVINA_DATABASE", "").strip()
    default_database = (PROJECT_ROOT / "instance" / "chamados.sqlite3").resolve()
    configured_database = Path(str(app.config["DATABASE"])).resolve()
    if not explicit_database or configured_database == default_database:
        raise click.ClickException(
            "Operação de demonstração bloqueada. Defina PREVINA_DATABASE para um arquivo SQLite exclusivo "
            "(por exemplo, instance/demonstracao.sqlite3). Nenhum dado foi alterado."
        )


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    secret = (test_config or {}).get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        secret_path = PROJECT_ROOT / "instance" / ".secret_key"
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        if not secret_path.exists():
            secret_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        secret = secret_path.read_text(encoding="utf-8").strip()

    app = Flask(
        __name__, instance_path=str(PROJECT_ROOT / "instance"), instance_relative_config=True,
        template_folder=str(PROJECT_ROOT / "templates"), static_folder=str(PROJECT_ROOT / "static"),
    )
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=secret,
        DATABASE=os.environ.get("PREVINA_DATABASE", str(PROJECT_ROOT / "instance" / "chamados.sqlite3")),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", False),
        WTF_CSRF_TIME_LIMIT=3600,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        DEMO_MODE=env_bool("DEMO_MODE", False),
        SUPPORT_KNOWLEDGE_BASE_URL=os.environ.get("SUPPORT_KNOWLEDGE_BASE_URL", "").strip(),
        SUPPORT_ON_CALL_URL=os.environ.get("SUPPORT_ON_CALL_URL", "").strip(),
        SUPPORT_HOURS_TEXT=os.environ.get("SUPPORT_HOURS_TEXT", "").strip(),
    )
    if test_config:
        app.config.update(test_config)

    CSRFProtect(app)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.before_request
    def load_logged_in_user() -> None:
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = get_db().execute(
                "SELECT id, username, full_name, role, active FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if g.user is None or not g.user["active"]:
                session.clear()
                g.user = None

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        user = getattr(g, "user", None)
        unread_count = 0
        if user:
            unread_count = int(get_db().execute(
                "SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)
            ).fetchone()["n"] or 0)
        shortcuts = []
        for key, label in (
            ("SUPPORT_KNOWLEDGE_BASE_URL", "Base de conhecimento"),
            ("SUPPORT_ON_CALL_URL", "Contato do plantão"),
        ):
            if app.config[key]:
                shortcuts.append({"url": app.config[key], "label": label})
        return {
            "app_title": APP_TITLE, "categories": CATEGORIES, "priorities": PRIORITIES,
            "statuses": STATUSES, "current_user": user, "unread_notification_count": unread_count,
            "support_shortcuts": shortcuts, "support_hours_text": app.config["SUPPORT_HOURS_TEXT"],
            "demo_mode": bool(app.config["DEMO_MODE"]),
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
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not getattr(g, "user", None):
                flash("Entre com seu usuário para continuar.", "warning")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def get_visible_ticket(ticket_id: int) -> sqlite3.Row:
        row = get_db().execute(
            """SELECT t.*,u.full_name requester_name,u.username requester_username
               FROM tickets t JOIN users u ON u.id=t.requester_id WHERE t.id=?""", (ticket_id,)
        ).fetchone()
        if row is None:
            abort(404)
        if g.user["role"] != "ti" and row["requester_id"] != g.user["id"]:
            abort(403)
        return row

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if getattr(g, "user", None):
            return redirect(url_for("index"))
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Usuário ou senha inválidos.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = True
                destination = request.args.get("next", "") or request.form.get("next", "")
                if destination.startswith("/") and not destination.startswith("//"):
                    return redirect(destination)
                return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        session.clear()
        flash("Sessão encerrada.", "success")
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index() -> Any:
        db = get_db()
        status_filter = request.args.get("status", "").strip()
        if status_filter not in STATUSES and status_filter != "finalizados":
            status_filter = ""
        search = request.args.get("q", "").strip()[:100]
        period = request.args.get("period", "all").strip()
        if period not in {value for value, _ in PERIOD_OPTIONS}:
            period = "all"

        # Escopo de permissão + período compartilhado entre KPIs e listagem.
        base: list[str] = []
        values: list[Any] = []
        if g.user["role"] != "ti":
            base.append("t.requester_id=?")
            values.append(g.user["id"])
        if not app.config["DEMO_MODE"]:
            base.append("COALESCE(t.is_demo,0)=0")
        if period in PERIOD_DAYS:
            base.append("t.created_at>=?")
            values.append((datetime.now() - timedelta(days=PERIOD_DAYS[period])).isoformat(timespec="seconds"))
        where = " WHERE " + " AND ".join(base) if base else ""
        agg = db.execute(
            f"""SELECT COUNT(*) total,
                SUM(CASE WHEN t.status='Aberto' THEN 1 ELSE 0 END) open_count,
                SUM(CASE WHEN t.status='Em andamento' THEN 1 ELSE 0 END) in_progress_count,
                SUM(CASE WHEN t.status='Aguardando usuário' THEN 1 ELSE 0 END) waiting_count,
                SUM(CASE WHEN t.status IN ('Resolvido','Encerrado') THEN 1 ELSE 0 END) resolved_closed_count
                FROM tickets t{where}""", values
        ).fetchone()
        summary = {key: int(agg[key] or 0) for key in (
            "total", "open_count", "in_progress_count", "waiting_count", "resolved_closed_count"
        )}
        grouped = db.execute(
            f"SELECT t.status,COUNT(*) amount FROM tickets t{where} GROUP BY t.status", values
        ).fetchall()
        status_counts = {status: 0 for status in STATUSES}
        for row in grouped:
            status_counts[row["status"]] = int(row["amount"] or 0)

        clauses, params = list(base), list(values)
        if status_filter == "finalizados":
            clauses.append("t.status IN ('Resolvido','Encerrado')")
        elif status_filter in STATUSES:
            clauses.append("t.status=?")
            params.append(status_filter)
        if search:
            like = f"%{search}%"
            clauses.append("(CAST(t.id AS TEXT) LIKE ? OR t.title LIKE ? OR t.category LIKE ? OR u.full_name LIKE ?)")
            params.extend([like, like, like, like])
        list_where = " WHERE " + " AND ".join(clauses) if clauses else ""
        tickets = db.execute(
            f"""SELECT t.id,t.title,t.category,t.priority,t.status,t.created_at,t.updated_at,
                       u.full_name requester_name
                FROM tickets t JOIN users u ON u.id=t.requester_id{list_where}
                ORDER BY CASE WHEN t.status IN ('Resolvido','Encerrado') THEN 1 ELSE 0 END,
                CASE t.priority WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 WHEN 'Normal' THEN 3 ELSE 4 END,
                t.updated_at DESC,t.id DESC""", params
        ).fetchall()
        return render_template(
            "index.html", tickets=tickets, summary=summary, status_counts=status_counts,
            status_filter=status_filter, search=search, period=period,
            period_options=PERIOD_OPTIONS, period_label=dict(PERIOD_OPTIONS)[period],
        )

    @app.route("/chamados/novo", methods=["GET", "POST"])
    @login_required
    def new_ticket() -> Any:
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "")
            priority = request.form.get("priority", "Normal")
            errors = []
            if not title or len(title) > 120: errors.append("Informe um título com até 120 caracteres.")
            if not description or len(description) > 4000: errors.append("Descreva o problema (até 4.000 caracteres).")
            if category not in CATEGORIES: errors.append("Selecione uma categoria válida.")
            if priority not in PRIORITIES: errors.append("Selecione uma prioridade válida.")
            if errors:
                for message in errors: flash(message, "error")
                return render_template("new_ticket.html", form=request.form), 400
            timestamp = now_text()
            db = get_db()
            cursor = db.execute(
                """INSERT INTO tickets(title,description,category,priority,status,requester_id,created_at,updated_at)
                   VALUES(?,?,?,?, 'Aberto',?,?,?)""",
                (title, description, category, priority, g.user["id"], timestamp, timestamp),
            )
            ticket_id = int(cursor.lastrowid or 0)
            db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                       (ticket_id, g.user["id"], "Chamado aberto.", timestamp))
            techs = db.execute("SELECT id FROM users WHERE role='ti' AND active=1 AND id<>?",
                               (g.user["id"],)).fetchall()
            target = url_for("ticket_detail", ticket_id=ticket_id)
            for tech in techs:
                add_notification(db, int(tech["id"]), ticket_id, "ticket-created",
                                 f"Novo chamado #{ticket_id}", f"{g.user['full_name']} abriu: {title}",
                                 target, timestamp)
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
                if g.user["role"] != "ti": abort(403)
                new_status = request.form.get("status", "")
                if new_status not in STATUSES:
                    flash("Status inválido.", "error")
                    return redirect(url_for("ticket_detail", ticket_id=ticket_id))
                if new_status != ticket["status"]:
                    timestamp = now_text()
                    db.execute("UPDATE tickets SET status=?,updated_at=? WHERE id=?", (new_status, timestamp, ticket_id))
                    db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                               (ticket_id, g.user["id"], f"Status alterado para: {new_status}.", timestamp))
                    if int(ticket["requester_id"]) != int(g.user["id"]):
                        add_notification(db, int(ticket["requester_id"]), ticket_id, "status-changed",
                                         "Status do chamado atualizado",
                                         f"O chamado #{ticket_id} agora está: {new_status}.",
                                         url_for("ticket_detail", ticket_id=ticket_id), timestamp)
                    db.commit()
                    flash("Status atualizado.", "success")
                else:
                    flash("O chamado já está nesse status.", "info")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))
            body = request.form.get("body", "").strip()
            if not body: flash("Digite uma mensagem antes de enviar.", "error")
            elif len(body) > 2000: flash("O comentário deve ter até 2.000 caracteres.", "error")
            else:
                timestamp = now_text()
                db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                           (ticket_id, g.user["id"], body, timestamp))
                db.execute("UPDATE tickets SET updated_at=? WHERE id=?", (timestamp, ticket_id))
                if g.user["role"] == "ti":
                    recipients = db.execute("SELECT id FROM users WHERE id=? AND active=1", (ticket["requester_id"],)).fetchall()
                else:
                    recipients = db.execute("SELECT id FROM users WHERE role='ti' AND active=1 AND id<>?",
                                            (g.user["id"],)).fetchall()
                for recipient in recipients:
                    if int(recipient["id"]) != int(g.user["id"]):
                        add_notification(db, int(recipient["id"]), ticket_id, "comment-added",
                                         f"Nova mensagem no chamado #{ticket_id}",
                                         f"{g.user['full_name']} adicionou uma mensagem ao chamado “{ticket['title']}”.",
                                         url_for("ticket_detail", ticket_id=ticket_id), timestamp)
                db.commit()
                flash("Comentário enviado.", "success")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))
        comments = db.execute(
            """SELECT c.id,c.body,c.created_at,u.full_name author_name,u.role author_role
               FROM comments c JOIN users u ON u.id=c.user_id WHERE c.ticket_id=? ORDER BY c.id""",
            (ticket_id,),
        ).fetchall()
        return render_template("ticket_detail.html", ticket=ticket, comments=comments,
                               is_technician=g.user["role"] == "ti")

    @app.get("/notificacoes")
    @login_required
    def notifications() -> Any:
        rows = get_db().execute(
            """SELECT n.*,t.title ticket_title FROM notifications n
               LEFT JOIN tickets t ON t.id=n.ticket_id WHERE n.user_id=?
               ORDER BY n.created_at DESC,n.id DESC LIMIT 100""", (g.user["id"],)
        ).fetchall()
        return render_template("notifications.html", notifications=rows)

    @app.get("/api/notificacoes/resumo")
    @login_required
    def notification_summary() -> Any:
        db = get_db()
        unread = db.execute("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=0",
                            (g.user["id"],)).fetchone()["n"]
        rows = db.execute(
            """SELECT id,title,message,kind,is_read,created_at FROM notifications
               WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 8""", (g.user["id"],)
        ).fetchall()
        return jsonify({
            "unread_count": int(unread or 0),
            "latest": [{
                "id": int(row["id"]), "title": row["title"], "message": row["message"],
                "kind": row["kind"], "is_read": bool(row["is_read"]),
                "created_at": row["created_at"],
                "open_url": url_for("open_notification", notification_id=int(row["id"])),
            } for row in rows],
        })

    @app.get("/notificacoes/<int:notification_id>/abrir")
    @login_required
    def open_notification(notification_id: int) -> Any:
        db = get_db()
        item = db.execute("SELECT target_url FROM notifications WHERE id=? AND user_id=?",
                          (notification_id, g.user["id"])).fetchone()
        if item is None: abort(404)
        db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
                   (notification_id, g.user["id"]))
        db.commit()
        target = item["target_url"] or url_for("notifications")
        return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("notifications"))

    @app.post("/notificacoes/<int:notification_id>/lida")
    @login_required
    def mark_notification_read(notification_id: int) -> Any:
        db = get_db()
        db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
                   (notification_id, g.user["id"]))
        db.commit()
        return redirect(url_for("notifications"))

    @app.post("/notificacoes/ler-todas")
    @login_required
    def mark_all_notifications_read() -> Any:
        db = get_db()
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (g.user["id"],))
        db.commit()
        flash("Notificações marcadas como lidas.", "success")
        return redirect(url_for("notifications"))

    @app.get("/health")
    def health() -> Any:
        get_db().execute("SELECT 1").fetchone()
        return {"app": "Previna Service Desk", "status": "ok"}

    @app.errorhandler(403)
    def forbidden(_error: Any) -> Any:
        return render_template("error.html", code=403,
                               message="Você não tem permissão para acessar este recurso."), 403

    @app.errorhandler(404)
    def not_found(_error: Any) -> Any:
        return render_template("error.html", code=404,
                               message="O recurso solicitado não foi encontrado."), 404

    @app.errorhandler(413)
    def file_too_large(_error: Any) -> Any:
        return render_template("error.html", code=413,
                               message="A solicitação excedeu o limite permitido."), 413

    @app.cli.command("create-user")
    @click.option("--username", prompt="Usuário de acesso")
    @click.option("--name", "full_name", prompt="Nome completo")
    @click.option("--role", type=click.Choice(["solicitante", "ti"], case_sensitive=False),
                  prompt="Perfil (solicitante/ti)")
    def create_user_command(username: str, full_name: str, role: str) -> None:
        username, full_name, role = username.strip().lower(), full_name.strip(), role.lower()
        if not username or len(username) > 50 or not full_name or len(full_name) > 100:
            raise click.ClickException("Usuário e nome são obrigatórios e devem respeitar os limites de tamanho.")
        password = click.prompt("Senha (mínimo 10 caracteres)", hide_input=True, confirmation_prompt=True)
        if len(password) < 10:
            raise click.ClickException("A senha deve ter pelo menos 10 caracteres.")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            raise click.ClickException("Esse nome de usuário já existe.")
        db.execute("""INSERT INTO users(username,full_name,password_hash,role,active,created_at)
                      VALUES(?,?,?,?,1,?)""",
                   (username, full_name, generate_password_hash(password), role, now_text()))
        db.commit()
        click.echo(f"Usuário '{username}' criado com perfil '{role}'.")

    @app.cli.command("list-users")
    def list_users_command() -> None:
        rows = get_db().execute("SELECT id,username,full_name,role,active FROM users ORDER BY full_name").fetchall()
        if not rows:
            click.echo("Nenhum usuário cadastrado. Execute: flask --app app:create_app create-user")
            return
        for row in rows:
            click.echo(f"{row['id']} | {row['username']} | {row['full_name']} | {row['role']} | ativo={row['active']}")

    @app.cli.command("seed-demo-data")
    def seed_demo_data_command() -> None:
        if not app.config["DEMO_MODE"]:
            raise click.ClickException("Bloqueado. Defina DEMO_MODE=true apenas em uma base de demonstração.")
        ensure_demo_database_isolated(app)
        password = os.environ.get("DEMO_SEED_PASSWORD", "")
        if len(password) < 10:
            raise click.ClickException("Defina DEMO_SEED_PASSWORD com no mínimo 10 caracteres.")
        db = get_db()
        users = {}
        for username, fullname, role in (
            ("demo_solicitante", "Solicitante de demonstração", "solicitante"),
            ("demo_atendente", "Atendente de demonstração", "ti"),
        ):
            row = db.execute("SELECT id,is_demo FROM users WHERE username=?", (username,)).fetchone()
            if row:
                if not row["is_demo"]:
                    raise click.ClickException(f"'{username}' já existe e não é demonstrativo. Nada foi alterado.")
                users[username] = int(row["id"])
            else:
                cur = db.execute(
                    """INSERT INTO users(username,full_name,password_hash,role,active,created_at,is_demo)
                       VALUES(?,?,?,?,1,?,1)""",
                    (username, fullname, generate_password_hash(password), role, now_text()),
                )
                users[username] = int(cur.lastrowid or 0)
        if int(db.execute("SELECT COUNT(*) n FROM tickets WHERE is_demo=1").fetchone()["n"] or 0) == 0:
            samples = (
                ("[DEMO] Notebook sem Wi-Fi", "Sem acesso à rede corporativa.", "Rede e internet", "Alta", "Aberto"),
                ("[DEMO] Instalação de aplicativo", "Solicitação de instalação.", "Sistemas e aplicativos", "Normal", "Em andamento"),
                ("[DEMO] Acesso a pasta", "Validar permissões.", "Acesso e senhas", "Baixa", "Aguardando usuário"),
                ("[DEMO] Impressora sem resposta", "Fila de impressão parada.", "Impressoras", "Normal", "Encerrado"),
            )
            for title, description, category, priority, status in samples:
                timestamp = now_text()
                cur = db.execute(
                    """INSERT INTO tickets(title,description,category,priority,status,requester_id,created_at,updated_at,is_demo)
                       VALUES(?,?,?,?,?,?,?,?,1)""",
                    (title, description, category, priority, status, users["demo_solicitante"], timestamp, timestamp),
                )
                ticket_id = int(cur.lastrowid or 0)
                db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                           (ticket_id, users["demo_solicitante"], "Registro fictício para demonstração.", timestamp))
                add_notification(db, users["demo_atendente"], ticket_id, "ticket-created",
                                 f"Chamado demonstrativo #{ticket_id}", title, f"/chamados/{ticket_id}", timestamp)
        db.commit()
        click.echo("Dados demonstrativos criados/verificados.")

    @app.cli.command("clean-demo-data")
    def clean_demo_data_command() -> None:
        if not app.config["DEMO_MODE"]:
            raise click.ClickException("Bloqueado. Defina DEMO_MODE=true antes de limpar dados demonstrativos.")
        ensure_demo_database_isolated(app)
        db = get_db()
        demo_users = [int(r["id"]) for r in db.execute("SELECT id FROM users WHERE is_demo=1")]
        demo_tickets = [int(r["id"]) for r in db.execute(
            "SELECT id FROM tickets WHERE is_demo=1" + (" OR requester_id IN (" + ",".join("?" for _ in demo_users) + ")" if demo_users else ""),
            demo_users,
        )]
        if not demo_users and not demo_tickets:
            click.echo("Nenhum dado de demonstração encontrado; banco não alterado.")
            return
        backup_path = backup_sqlite_database(Path(app.config["DATABASE"]))
        ticket_marks = ",".join("?" for _ in demo_tickets)
        if demo_tickets:
            db.execute(f"DELETE FROM notifications WHERE ticket_id IN ({ticket_marks})", demo_tickets)
            db.execute(f"DELETE FROM comments WHERE ticket_id IN ({ticket_marks})", demo_tickets)
        if demo_users:
            user_marks = ",".join("?" for _ in demo_users)
            db.execute(f"DELETE FROM notifications WHERE user_id IN ({user_marks})", demo_users)
            db.execute(f"DELETE FROM comments WHERE user_id IN ({user_marks})", demo_users)
        if demo_tickets:
            db.execute(f"DELETE FROM tickets WHERE id IN ({ticket_marks})", demo_tickets)
        if demo_users:
            user_marks = ",".join("?" for _ in demo_users)
            db.execute(f"DELETE FROM users WHERE id IN ({user_marks})", demo_users)
        db.commit()
        click.echo(f"Dados de demonstração removidos. Backup criado em: {backup_path}")

    return app
