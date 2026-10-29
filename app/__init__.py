from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
import sqlite3
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import click
from flask import Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
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
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_ACCOUNT_FAILURES = 5
LOGIN_MAX_IP_FAILURES = 30
TICKETS_PAGE_SIZE = 25


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def now_text() -> str:
    """Timestamp novo em UTC explícito; registros históricos não são regravados."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # Timestamps sem offset são legados e interpretados como horário de São Paulo.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SAO_PAULO)
    return parsed


def timestamp_epoch(value: str | None) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).timestamp()


def is_safe_local_redirect(target: str | None) -> bool:
    """Aceita somente destinos locais absolutos e rejeita variantes ambíguas."""
    if (
        not target or target != target.strip() or target.startswith("//")
        or "\\" in target
    ):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in target):
        return False
    if re.match(r"^/(?:%2f|%5c)", target, flags=re.IGNORECASE):
        return False
    try:
        parsed = urlsplit(target)
    except ValueError:
        return False
    return (
        not parsed.scheme
        and not parsed.netloc
        and parsed.path.startswith("/")
        and not parsed.path.startswith("//")
    )


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = Path(str(current_app_config_database()))
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        # Normalize timestamps for comparisons without rewriting legacy records.
        connection.create_function("utc_epoch", 1, timestamp_epoch, deterministic=True)
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


def migrate_database(db: sqlite3.Connection) -> None:
    """Migra o SQLite de forma aditiva, preservando usuários e chamados."""
    additions = {
        "users": {"is_demo": "INTEGER NOT NULL DEFAULT 0", "email": "TEXT"},
        "tickets": {"is_demo": "INTEGER NOT NULL DEFAULT 0"},
        "notifications": {
            "actor_user_id": "INTEGER REFERENCES users(id) ON DELETE SET NULL",
            "email_status": "TEXT NOT NULL DEFAULT 'pending'",
            "email_error": "TEXT",
            "read_at": "TEXT",
        },
    }
    for table, columns_to_add in additions.items():
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, definition in columns_to_add.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read, id);
        CREATE INDEX IF NOT EXISTS idx_notifications_ticket ON notifications(ticket_id, id);
        CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at, id);
        CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type, id);
        """
    )


def init_db() -> None:
    db = get_db()
    db.executescript((PROJECT_ROOT / "schema.sql").read_text(encoding="utf-8"))
    migrate_database(db)
    db.commit()


def add_notification(db: sqlite3.Connection, user_id: int, ticket_id: int | None,
                     kind: str, title: str, message: str, target_url: str, created_at: str) -> None:
    db.execute(
        """INSERT INTO notifications(user_id,ticket_id,kind,title,message,target_url,is_read,created_at)
           VALUES(?,?,?,?,?,?,0,?)""",
        (user_id, ticket_id, kind, title[:160], message[:500], target_url[:500], created_at),
    )


def audit_event(event_type: str, description: str, actor_user_id: int | None = None,
                ticket_id: int | None = None) -> None:
    """Registra eventos relevantes sem guardar senhas ou corpo de comentários."""
    db = get_db()
    db.execute(
        """INSERT INTO audit_logs(actor_user_id,ticket_id,event_type,description,ip_address,user_agent,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (actor_user_id, ticket_id, event_type[:60], description[:500],
         (request.remote_addr or "")[:64], (request.headers.get("User-Agent") or "")[:300], now_text()),
    )
    db.commit()


def send_email(recipient: str | None, subject: str, body: str) -> tuple[str, str | None]:
    """Envia e-mail quando SMTP está configurado; a notificação interna não depende disso."""
    recipient = (recipient or "").strip()
    if not recipient:
        return "no_email", None
    host = str(current_app.config.get("SMTP_HOST") or "").strip()
    sender = str(current_app.config.get("SMTP_FROM") or "").strip()
    if not host or not sender:
        return "not_configured", None
    subject = re.sub(r"[\r\n]+", " ", subject)[:180]
    port = int(current_app.config.get("SMTP_PORT", 587))
    username = str(current_app.config.get("SMTP_USERNAME") or "").strip()
    password = str(current_app.config.get("SMTP_PASSWORD") or "")
    use_ssl = bool(current_app.config.get("SMTP_USE_SSL", False))
    use_tls = bool(current_app.config.get("SMTP_USE_TLS", True)) and not use_ssl
    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=5, context=ssl.create_default_context()) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=5) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        return "sent", None
    except Exception as exc:
        current_app.logger.warning("Falha no envio SMTP de notificação: %s", type(exc).__name__)
        return "failed", type(exc).__name__


def create_notification(target_user_id: int, actor_user_id: int | None, ticket_id: int | None,
                        kind: str, title: str, message: str, target_url: str,
                        email_subject: str | None = None, email_body: str | None = None) -> int:
    db = get_db()
    recipient = db.execute(
        "SELECT email,full_name FROM users WHERE id=? AND active=1", (target_user_id,)
    ).fetchone()
    if recipient is None:
        return 0
    timestamp = now_text()
    cursor = db.execute(
        """INSERT INTO notifications
           (user_id,actor_user_id,ticket_id,kind,title,message,target_url,is_read,email_status,created_at)
           VALUES(?,?,?,?,?,?,?,0,'pending',?)""",
        (target_user_id, actor_user_id, ticket_id, kind[:40], title[:160], message[:500], target_url[:500], timestamp),
    )
    notification_id = int(cursor.lastrowid or 0)
    db.commit()
    body = email_body or (
        f"Olá, {recipient['full_name']}.\n\n{message}\n\n"
        "Acesse a Central de Chamados da Previna para consultar os detalhes."
    )
    base_url = str(current_app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base_url and target_url.startswith("/") and not target_url.startswith("//"):
        body += f"\n\nAcessar no sistema: {base_url}{target_url}"
    status, error = send_email(recipient["email"], email_subject or f"[Previna Service Desk] {title}", body)
    db.execute("UPDATE notifications SET email_status=?,email_error=? WHERE id=?",
               (status, error, notification_id))
    db.commit()
    return notification_id


def notify_users(target_user_ids: list[int], *, actor_user_id: int | None, ticket_id: int | None,
                 kind: str, title: str, message: str, target_url: str,
                 email_subject: str | None = None, email_body: str | None = None,
                 exclude_actor: bool = True) -> None:
    for user_id in sorted(set(target_user_ids)):
        if exclude_actor and actor_user_id is not None and user_id == actor_user_id:
            continue
        create_notification(
            target_user_id=user_id, actor_user_id=actor_user_id, ticket_id=ticket_id,
            kind=kind, title=title, message=message, target_url=target_url,
            email_subject=email_subject, email_body=email_body,
        )


def active_user_ids_by_role(role: str) -> list[int]:
    rows = get_db().execute("SELECT id FROM users WHERE role=? AND active=1 ORDER BY id", (role,)).fetchall()
    return [int(row["id"]) for row in rows]


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
    app_env = os.environ.get("PREVINA_ENV", "development").strip().lower()
    configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if app_env in {"prod", "production"} and not configured_secret:
        raise RuntimeError(
            "Em produção, configure FLASK_SECRET_KEY com um segredo persistente antes de iniciar."
        )
    secret = (test_config or {}).get("SECRET_KEY") or configured_secret
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
        SESSION_COOKIE_SECURE=(app_env in {"prod", "production"} or env_bool("SESSION_COOKIE_SECURE", False)),
        WTF_CSRF_TIME_LIMIT=3600,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        DEMO_MODE=env_bool("DEMO_MODE", False),
        APP_BASE_URL=os.environ.get("PREVINA_BASE_URL", "").strip().rstrip("/"),
        SMTP_HOST=os.environ.get("PREVINA_SMTP_HOST", "").strip(),
        SMTP_PORT=int(os.environ.get("PREVINA_SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("PREVINA_SMTP_USERNAME", "").strip(),
        SMTP_PASSWORD=os.environ.get("PREVINA_SMTP_PASSWORD", ""),
        SMTP_FROM=os.environ.get("PREVINA_SMTP_FROM", "").strip(),
        SMTP_USE_TLS=env_bool("PREVINA_SMTP_USE_TLS", True),
        SMTP_USE_SSL=env_bool("PREVINA_SMTP_USE_SSL", False),
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
                "SELECT id, username, full_name, role, active, email FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if g.user is None or not g.user["active"]:
                session.clear()
                g.user = None

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        user = getattr(g, "user", None)
        unread_count = 0
        latest_notification_id = 0
        if user:
            db = get_db()
            stats = db.execute(
                "SELECT COUNT(*) unread_count FROM notifications WHERE user_id=? AND is_read=0",
                (user["id"],),
            ).fetchone()
            unread_count = int(stats["unread_count"] or 0)
            latest_notification_id = int(db.execute(
                "SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?", (user["id"],)
            ).fetchone()[0] or 0)
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
            "latest_notification_id": latest_notification_id, "support_shortcuts": shortcuts,
            "support_hours_text": app.config["SUPPORT_HOURS_TEXT"], "demo_mode": bool(app.config["DEMO_MODE"]),
        }

    @app.template_filter("datetime_br")
    def datetime_br(value: str | None) -> str:
        if not value:
            return "—"
        parsed = parse_timestamp(value)
        if parsed is None:
            return str(value)
        return parsed.astimezone(SAO_PAULO).strftime("%d/%m/%Y às %H:%M")

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
            """SELECT t.*,u.full_name requester_name,u.username requester_username,u.email requester_email
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
            username = request.form.get("username", "").strip().lower()[:50]
            password = request.form.get("password", "")
            db = get_db()
            account_hash = hmac.new(
                str(current_app.config["SECRET_KEY"]).encode("utf-8"),
                username.encode("utf-8"), hashlib.sha256,
            ).hexdigest()[:24]
            window_start = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).timestamp()
            remote_ip = (request.remote_addr or "")[:64]
            recent = db.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN description LIKE ? THEN 1 ELSE 0 END) account_total
                   FROM audit_logs
                   WHERE event_type='login_failed' AND ip_address=? AND utc_epoch(created_at)>=?""",
                (f"%account_hash={account_hash}%", remote_ip, window_start),
            ).fetchone()
            ip_failures = int(recent["total"] or 0)
            account_failures = int(recent["account_total"] or 0)
            if ip_failures >= LOGIN_MAX_IP_FAILURES or account_failures >= LOGIN_MAX_ACCOUNT_FAILURES:
                audit_event(
                    "login_throttled",
                    "Tentativa de login bloqueada temporariamente pelo limite de segurança.",
                )
                flash("Não foi possível autenticar agora. Aguarde alguns minutos e tente novamente.", "error")
                return render_template("login.html"), 429

            user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                audit_event(
                    "login_failed",
                    f"Tentativa de login malsucedida; account_hash={account_hash}.",
                    int(user["id"]) if user else None,
                )
                flash("Usuário ou senha inválidos.", "error")
            else:
                session.clear()
                session["user_id"] = int(user["id"])
                session.permanent = True
                audit_event("login_success", f"{user['full_name']} entrou no sistema.", int(user["id"]))
                notify_users(
                    active_user_ids_by_role("ti"), actor_user_id=int(user["id"]), ticket_id=None,
                    kind="login", title="Entrada no sistema",
                    message=f"{user['full_name']} entrou na Central de Chamados.",
                    target_url=url_for("audit_log"),
                    email_subject="[Previna Service Desk] Entrada de usuário",
                    email_body=f"Usuário: {user['username']}\nHorário: {now_text()}",
                    exclude_actor=True,
                )
                destination = request.args.get("next", "") or request.form.get("next", "")
                if is_safe_local_redirect(destination):
                    return redirect(destination)
                return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        user_id = int(g.user["id"])
        username = str(g.user["username"])
        full_name = str(g.user["full_name"])
        role = str(g.user["role"])
        session.clear()
        try:
            audit_event("logout", f"{full_name} encerrou a sessão.", user_id)
            notify_users(
                active_user_ids_by_role("ti"), actor_user_id=user_id, ticket_id=None,
                kind="logout", title="Saída do sistema",
                message=f"{full_name} encerrou a sessão ({username}).",
                target_url=url_for("audit_log"),
                email_subject="[Previna Service Desk] Saída de usuário",
                email_body=f"{full_name} encerrou a sessão.\nHorário: {now_text()}\nPerfil: {role}",
                exclude_actor=True,
            )
        except Exception:
            current_app.logger.exception("Não foi possível registrar a saída; a sessão já foi encerrada.")
        flash("Sessão encerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/perfil", methods=["GET", "POST"])
    @login_required
    def profile() -> Any:
        db = get_db()
        user_id = int(g.user["id"])
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            errors = []
            if not full_name or len(full_name) > 100:
                errors.append("Informe um nome completo com até 100 caracteres.")
            if email and (len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)):
                errors.append("Informe um endereço de e-mail válido ou deixe o campo vazio.")
            row = db.execute(
                "SELECT id,username,full_name,role,email,active FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if errors:
                for message in errors:
                    flash(message, "error")
                profile_data = dict(row)
                profile_data["full_name"] = full_name
                profile_data["email"] = email
                return render_template("profile.html", profile=profile_data), 400
            db.execute("UPDATE users SET full_name=?,email=? WHERE id=?", (full_name, email or None, user_id))
            db.commit()
            try:
                audit_event("profile_updated", f"{full_name} atualizou o próprio perfil.", user_id)
            except Exception:
                current_app.logger.exception("Não foi possível registrar a alteração de perfil.")
            flash("Perfil atualizado com sucesso.", "success")
            return redirect(url_for("profile"))
        row = db.execute(
            "SELECT id,username,full_name,role,email,active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if row is None:
            session.clear()
            return redirect(url_for("login"))
        return render_template("profile.html", profile=row)

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
            base.append("utc_epoch(t.created_at)>=?")
            values.append((datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[period])).timestamp())
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
        filtered_total = int(db.execute(
            f"SELECT COUNT(*) FROM tickets t JOIN users u ON u.id=t.requester_id{list_where}",
            params,
        ).fetchone()[0] or 0)
        try:
            page = max(1, int(request.args.get("page", "1")))
        except (TypeError, ValueError):
            page = 1
        page_count = max(1, (filtered_total + TICKETS_PAGE_SIZE - 1) // TICKETS_PAGE_SIZE)
        page = min(page, page_count)
        tickets = db.execute(
            f"""SELECT t.id,t.title,t.category,t.priority,t.status,t.created_at,t.updated_at,
                       u.full_name requester_name
                FROM tickets t JOIN users u ON u.id=t.requester_id{list_where}
                ORDER BY CASE WHEN t.status IN ('Resolvido','Encerrado') THEN 1 ELSE 0 END,
                CASE t.priority WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 WHEN 'Normal' THEN 3 ELSE 4 END,
                utc_epoch(t.updated_at) DESC,t.id DESC
                LIMIT ? OFFSET ?""",
            [*params, TICKETS_PAGE_SIZE, (page - 1) * TICKETS_PAGE_SIZE],
        ).fetchall()
        return render_template(
            "index.html", tickets=tickets, summary=summary, status_counts=status_counts,
            status_filter=status_filter, search=search, period=period,
            period_options=PERIOD_OPTIONS, period_label=dict(PERIOD_OPTIONS)[period],
            page=page, page_count=page_count, filtered_total=filtered_total,
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
            db.commit()

            audit_event("ticket_created", f"Chamado #{ticket_id} criado: {title}.", int(g.user["id"]), ticket_id)
            create_notification(
                target_user_id=int(g.user["id"]), actor_user_id=int(g.user["id"]), ticket_id=ticket_id,
                kind="ticket_created", title=f"Chamado #{ticket_id} registrado",
                message=f"Seu chamado #{ticket_id} ('{title}') foi registrado com status Aberto.",
                target_url=target,
                email_subject=f"[Previna Service Desk] Confirmação do chamado #{ticket_id}",
            )
            notify_users(
                [int(row["id"]) for row in techs], actor_user_id=int(g.user["id"]), ticket_id=ticket_id,
                kind="ticket_created", title=f"Novo chamado #{ticket_id}",
                message=f"{g.user['full_name']} abriu o chamado #{ticket_id}: {title}. Prioridade: {priority}.",
                target_url=target, email_subject=f"[Previna Service Desk] Novo chamado #{ticket_id}",
                email_body=f"{g.user['full_name']} abriu o chamado #{ticket_id}: {title}.\nCategoria: {category}\nPrioridade: {priority}",
            )
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
                    old_status = ticket["status"]
                    timestamp = now_text()
                    db.execute("UPDATE tickets SET status=?,updated_at=? WHERE id=?",
                               (new_status, timestamp, ticket_id))
                    db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                               (ticket_id, g.user["id"], f"Status alterado para: {new_status}.", timestamp))
                    db.commit()
                    audit_event("status_changed",
                                f"Status do chamado #{ticket_id} alterado de '{old_status}' para '{new_status}'.",
                                int(g.user["id"]), ticket_id)
                    notify_users(
                        [int(ticket["requester_id"])], actor_user_id=int(g.user["id"]), ticket_id=ticket_id,
                        kind="status_changed", title=f"Atualização do chamado #{ticket_id}",
                        message=f"O status mudou de '{old_status}' para '{new_status}'.",
                        target_url=url_for("ticket_detail", ticket_id=ticket_id),
                        email_subject=f"[Previna Service Desk] Atualização do chamado #{ticket_id}",
                    )
                    flash("Status atualizado. O solicitante foi notificado.", "success")
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
                db.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",
                           (ticket_id, g.user["id"], body, timestamp))
                db.execute("UPDATE tickets SET updated_at=? WHERE id=?", (timestamp, ticket_id))
                db.commit()
                audit_event("comment_added", f"Comentário registrado no chamado #{ticket_id}.",
                            int(g.user["id"]), ticket_id)
                recipients = active_user_ids_by_role("ti") if int(g.user["id"]) == int(ticket["requester_id"]) else [int(ticket["requester_id"])]
                notify_users(
                    recipients, actor_user_id=int(g.user["id"]), ticket_id=ticket_id,
                    kind="comment_added", title=f"Nova mensagem no chamado #{ticket_id}",
                    message=f"{g.user['full_name']} adicionou uma mensagem ao chamado “{ticket['title']}”.",
                    target_url=url_for("ticket_detail", ticket_id=ticket_id),
                    email_subject=f"[Previna Service Desk] Nova mensagem no chamado #{ticket_id}",
                )
                flash("Comentário enviado. A outra parte foi notificada.", "success")
                return redirect(url_for("ticket_detail", ticket_id=ticket_id))

        comments = db.execute(
            """SELECT c.id,c.body,c.created_at,u.full_name author_name,u.role author_role
               FROM comments c JOIN users u ON u.id=c.user_id WHERE c.ticket_id=? ORDER BY c.id""",
            (ticket_id,),
        ).fetchall()
        return render_template("ticket_detail.html", ticket=ticket, comments=comments,
                               is_technician=g.user["role"] == "ti")

    @app.post("/chamados/<int:ticket_id>/excluir")
    @login_required
    def delete_ticket(ticket_id: int) -> Any:
        if g.user["role"] != "ti":
            abort(403)

        db = get_db()
        ticket = db.execute(
            "SELECT id,title FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            abort(404)

        actor_user_id = int(g.user["id"])
        actor_name = str(g.user["full_name"])
        timestamp = now_text()
        description = f"{actor_name} excluiu o chamado #{ticket_id}: {ticket['title']}."
        try:
            db.execute(
                """INSERT INTO audit_logs
                   (actor_user_id,ticket_id,event_type,description,ip_address,user_agent,created_at)
                   VALUES(?,?,'ticket_deleted',?,?,?,?)""",
                (actor_user_id, ticket_id, description, (request.remote_addr or "")[:64],
                 (request.headers.get("User-Agent") or "")[:300], timestamp),
            )
            db.execute(
                """UPDATE notifications
                   SET ticket_id=NULL, target_url=?, title='Chamado removido',
                       message='O chamado associado a esta notificação foi excluído.'
                   WHERE ticket_id=?""",
                (url_for("notifications"), ticket_id),
            )
            db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
            db.commit()
        except sqlite3.Error:
            db.rollback()
            current_app.logger.exception("Falha ao excluir o chamado #%s.", ticket_id)
            flash("Não foi possível excluir o chamado. Nenhum dado foi alterado.", "error")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))

        flash(f"Chamado #{ticket_id} excluído com sucesso.", "success")
        return redirect(url_for("index"))

    @app.route("/notificacoes", methods=["GET", "POST"])
    @login_required
    def notifications() -> Any:
        db = get_db()
        if request.method == "POST":
            db.execute("UPDATE notifications SET is_read=1,read_at=? WHERE user_id=? AND is_read=0",
                       (now_text(), g.user["id"]))
            db.commit()
            flash("Todas as notificações foram marcadas como lidas.", "success")
            return redirect(url_for("notifications"))
        rows = db.execute(
            """SELECT n.*,t.title ticket_title,actor.full_name actor_name
               FROM notifications n
               LEFT JOIN tickets t ON t.id=n.ticket_id
               LEFT JOIN users actor ON actor.id=n.actor_user_id
               WHERE n.user_id=? ORDER BY n.created_at DESC,n.id DESC LIMIT 200""",
            (g.user["id"],),
        ).fetchall()
        read_count = int(db.execute(
            "SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=1",
            (g.user["id"],),
        ).fetchone()["n"] or 0)
        return render_template(
            "notifications.html",
            notifications=rows,
            read_notification_count=read_count,
        )

    @app.get("/api/notificacoes/resumo")
    @login_required
    def notification_summary() -> Any:
        db = get_db()
        try:
            after_id = max(0, int(request.args.get("after_id", "0")))
        except (TypeError, ValueError):
            after_id = 0
        unread = int(db.execute(
            "SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=0", (g.user["id"],)
        ).fetchone()["n"] or 0)
        latest = db.execute(
            """SELECT id,title,message,kind,is_read,created_at,target_url FROM notifications
               WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 8""", (g.user["id"],)
        ).fetchall()
        new_rows = db.execute(
            """SELECT id,title,message,kind,is_read,created_at,target_url FROM notifications
               WHERE user_id=? AND id>? ORDER BY id ASC LIMIT 10""", (g.user["id"], after_id)
        ).fetchall() if after_id else []
        latest_id = int(db.execute(
            "SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?", (g.user["id"],)
        ).fetchone()[0] or 0)
        def payload(row: sqlite3.Row) -> dict[str, Any]:
            return {
                "id": int(row["id"]), "title": row["title"], "message": row["message"],
                "kind": row["kind"], "is_read": bool(row["is_read"]), "created_at": row["created_at"],
                "open_url": url_for("open_notification", notification_id=int(row["id"])),
                "target_url": row["target_url"],
            }
        latest_payload = [payload(row) for row in latest]
        new_payload = [payload(row) for row in new_rows]
        return jsonify({
            "unread_count": unread, "latest_id": latest_id,
            "latest": latest_payload, "notifications": new_payload,
        })

    @app.route("/notificacoes/<int:notification_id>/abrir", methods=["GET", "POST"])
    @login_required
    def open_notification(notification_id: int) -> Any:
        db = get_db()
        row = db.execute(
            "SELECT target_url FROM notifications WHERE id=? AND user_id=?",
            (notification_id, g.user["id"]),
        ).fetchone()
        if row is None:
            abort(404)
        db.execute("UPDATE notifications SET is_read=1,read_at=? WHERE id=? AND user_id=?",
                   (now_text(), notification_id, g.user["id"]))
        db.commit()
        target = row["target_url"] or url_for("notifications")
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("notifications")
        return redirect(target)

    @app.post("/notificacoes/<int:notification_id>/lida")
    @login_required
    def mark_notification_read(notification_id: int) -> Any:
        db = get_db()
        db.execute("UPDATE notifications SET is_read=1,read_at=? WHERE id=? AND user_id=?",
                   (now_text(), notification_id, g.user["id"]))
        db.commit()
        return redirect(url_for("notifications"))

    @app.post("/notificacoes/ler-todas")
    @login_required
    def mark_all_notifications_read() -> Any:
        db = get_db()
        db.execute("UPDATE notifications SET is_read=1,read_at=? WHERE user_id=? AND is_read=0",
                   (now_text(), g.user["id"]))
        db.commit()
        flash("Notificações marcadas como lidas.", "success")
        return redirect(url_for("notifications"))

    @app.post("/notificacoes/<int:notification_id>/excluir")
    @login_required
    def delete_notification(notification_id: int) -> Any:
        db = get_db()
        try:
            cursor = db.execute(
                "DELETE FROM notifications WHERE id=? AND user_id=?",
                (notification_id, g.user["id"]),
            )
            if cursor.rowcount != 1:
                db.rollback()
                abort(404)
            db.commit()
        except sqlite3.Error:
            db.rollback()
            current_app.logger.exception("Falha ao excluir a notificação #%s.", notification_id)
            flash("Não foi possível excluir a notificação.", "error")
            return redirect(url_for("notifications"))

        flash("Notificação excluída com sucesso.", "success")
        return redirect(url_for("notifications"))

    @app.post("/notificacoes/limpar-lidas")
    @login_required
    def clear_read_notifications() -> Any:
        db = get_db()
        try:
            cursor = db.execute(
                "DELETE FROM notifications WHERE user_id=? AND is_read=1",
                (g.user["id"],),
            )
            deleted_count = max(0, int(cursor.rowcount))
            db.commit()
        except sqlite3.Error:
            db.rollback()
            current_app.logger.exception("Falha ao limpar notificações lidas do usuário atual.")
            flash("Não foi possível limpar as notificações lidas.", "error")
            return redirect(url_for("notifications"))

        if deleted_count == 1:
            flash("1 notificação lida excluída.", "success")
        else:
            flash(f"{deleted_count} notificações lidas excluídas.", "success")
        return redirect(url_for("notifications"))

    @app.get("/auditoria")
    @login_required
    def audit_log() -> Any:
        if g.user["role"] != "ti":
            abort(403)
        rows = get_db().execute(
            """SELECT a.*,u.full_name actor_name,u.username actor_username
               FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_user_id
               ORDER BY a.id DESC LIMIT 250"""
        ).fetchall()
        return render_template("audit.html", audit_rows=rows)

    @app.cli.command("set-email")
    @click.option("--username", prompt="Usuário de acesso")
    @click.option("--email", prompt="Novo e-mail (deixe vazio para remover)", default="", show_default=False)
    def set_email_command(username: str, email: str) -> None:
        username, email = username.strip().lower(), email.strip().lower()
        if email and (len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)):
            raise click.ClickException("Informe um e-mail válido ou deixe o campo vazio.")
        db = get_db()
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if user is None:
            raise click.ClickException("Usuário não encontrado.")
        db.execute("UPDATE users SET email=? WHERE id=?", (email or None, user["id"]))
        db.commit()
        click.echo(f"E-mail atualizado para '{username}'." if email else f"E-mail removido de '{username}'.")

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
    @click.option("--email", default="", prompt="E-mail (opcional)", show_default=False)
    def create_user_command(username: str, full_name: str, role: str, email: str) -> None:
        username, full_name, role, email = username.strip().lower(), full_name.strip(), role.lower(), email.strip().lower()
        if not username or len(username) > 50 or not full_name or len(full_name) > 100:
            raise click.ClickException("Usuário e nome são obrigatórios e devem respeitar os limites de tamanho.")
        if email and (len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)):
            raise click.ClickException("Informe um e-mail válido ou deixe o campo vazio.")
        password = click.prompt("Senha (mínimo 10 caracteres)", hide_input=True, confirmation_prompt=True)
        if len(password) < 10:
            raise click.ClickException("A senha deve ter pelo menos 10 caracteres.")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            raise click.ClickException("Esse nome de usuário já existe.")
        db.execute("""INSERT INTO users(username,full_name,password_hash,role,active,created_at,email)
                      VALUES(?,?,?,?,1,?,?)""",
                   (username, full_name, generate_password_hash(password), role, now_text(), email or None))
        db.commit()
        click.echo(f"Usuário '{username}' criado com perfil '{role}'.")

    @app.cli.command("list-users")
    def list_users_command() -> None:
        rows = get_db().execute("SELECT id,username,full_name,role,active,email FROM users ORDER BY full_name").fetchall()
        if not rows:
            click.echo("Nenhum usuário cadastrado. Execute: flask --app app:create_app create-user")
            return
        for row in rows:
            click.echo(f"{row['id']} | {row['username']} | {row['full_name']} | {row['role']} | ativo={row['active']} | e-mail={row['email'] or '(não cadastrado)'}")

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
