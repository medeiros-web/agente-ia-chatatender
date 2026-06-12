"""
Camada de dados — SQLite com WAL mode.
Substitui sessions.py com schema multi-tenant completo:
tickets, contacts, messages, users, queues.
"""
import sqlite3
import hashlib
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import os as _os
DB_PATH = Path(_os.environ.get("DB_PATH", str(Path.home() / "meu-agente" / "dados.sqlite")))


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _now() -> str:
    return datetime.utcnow().isoformat()


def _hash_pw(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def _check_pw(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split(":", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == h
    except Exception:
        return False


# ── Inicialização ─────────────────────────────────────────────────────────────

def _migrate(conn: sqlite3.Connection) -> None:
    """Migra schema antigo para o novo sem perder dados."""
    # Verifica se messages tem ticket_id; se não, recria a tabela
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if cols and "ticket_id" not in cols:
        conn.executescript("""
        ALTER TABLE messages RENAME TO messages_old;
        CREATE TABLE messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id   INTEGER NOT NULL DEFAULT 0,
            body        TEXT    NOT NULL,
            from_me     INTEGER DEFAULT 0,
            media_type  TEXT,
            media_url   TEXT,
            status      TEXT DEFAULT 'sent',
            created_at  TEXT NOT NULL
        );
        INSERT INTO messages (id, ticket_id, body, from_me, created_at)
        SELECT id, 0, content, CASE WHEN role='assistant' THEN 1 ELSE 0 END,
               datetime(ts, 'unixepoch')
        FROM messages_old;
        DROP TABLE messages_old;
        """)

    # Verifica se sessions existe e não interfere
    if "sessions" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        pass  # mantém para compatibilidade com código antigo

    conn.commit()


def init_db() -> None:
    conn = _db()
    # Migração antes de criar novas tabelas
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "messages" in existing:
        _migrate(conn)

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT    NOT NULL,
        email        TEXT    UNIQUE NOT NULL,
        password_hash TEXT   NOT NULL,
        role         TEXT    DEFAULT 'agent',
        active       INTEGER DEFAULT 1,
        created_at   TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS queues (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        color        TEXT DEFAULT '#00a884',
        greeting     TEXT DEFAULT '',
        created_at   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS contacts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        phone        TEXT UNIQUE NOT NULL,
        name         TEXT,
        profile_pic  TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tickets (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id      INTEGER NOT NULL REFERENCES contacts(id),
        queue_id        INTEGER REFERENCES queues(id),
        assigned_to     INTEGER REFERENCES users(id),
        status          TEXT DEFAULT 'waiting',
        ai_enabled      INTEGER DEFAULT 1,
        unread_count    INTEGER DEFAULT 0,
        last_message    TEXT DEFAULT '',
        last_message_at TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
        body        TEXT    NOT NULL,
        from_me     INTEGER DEFAULT 0,
        media_type  TEXT,
        media_url   TEXT,
        status      TEXT DEFAULT 'sent',
        created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tickets_status     ON tickets(status);
    CREATE INDEX IF NOT EXISTS idx_tickets_contact    ON tickets(contact_id);
    CREATE INDEX IF NOT EXISTS idx_messages_ticket    ON messages(ticket_id);
    """)

    # Admin padrão
    row = conn.execute("SELECT id FROM users WHERE email='medeirosassessor.adv@gmail.com'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users (name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
            ("admin", "medeirosassessor.adv@gmail.com", _hash_pw("Aa213780@"), "admin", _now()),
        )
    # Remove credencial antiga se existir (novo admin já foi criado acima)
    conn.execute("DELETE FROM users WHERE email='admin@chatatender.com'")

    # Fila padrão
    if not conn.execute("SELECT id FROM queues").fetchone():
        conn.execute(
            "INSERT INTO queues (name,color,created_at) VALUES(?,?,?)",
            ("Suporte", "#00a884", _now()),
        )

    conn.commit()
    conn.close()


# ── Usuários ──────────────────────────────────────────────────────────────────

def authenticate_user(email: str, password: str) -> Optional[dict]:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM users WHERE email=? AND active=1", (email,)
    ).fetchone()
    conn.close()
    if row and _check_pw(password, row["password_hash"]):
        return dict(row)
    return None


def get_users() -> list[dict]:
    conn = _db()
    rows = conn.execute("SELECT id,name,email,role,active,created_at FROM users ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(name: str, email: str, password: str, role: str = "agent") -> dict:
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO users (name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,1,?)",
            (name.strip(), email.strip().lower(), _hash_pw(password), role, _now()),
        )
        uid = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT id,name,email,role,active,created_at FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        return {"ok": True, "user": dict(row)}
    except sqlite3.IntegrityError:
        conn.close()
        return {"ok": False, "error": "E-mail já cadastrado"}


def update_user(uid: int, **kwargs) -> dict:
    allowed = {"name", "email", "role", "active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if "password" in kwargs and kwargs["password"]:
        fields["password_hash"] = _hash_pw(kwargs["password"])
    if not fields:
        return {"ok": False, "error": "Nenhum campo válido"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [uid]
    conn.execute(f"UPDATE users SET {sets} WHERE id=?", vals)
    conn.commit()
    row = conn.execute("SELECT id,name,email,role,active,created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return {"ok": True, "user": dict(row) if row else None}


def delete_user(uid: int) -> dict:
    conn = _db()
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True}


def bulk_import_contacts(contacts: list[dict]) -> dict:
    """Importa lista de contatos [{phone, name}]. Retorna estatísticas."""
    now = _now()
    inserted = 0
    updated = 0
    skipped = 0
    conn = _db()
    for c in contacts:
        phone = str(c.get("phone", "")).strip().replace("+", "").replace(" ", "").replace("-", "")
        name  = str(c.get("name", "")).strip() or phone
        if not phone or len(phone) < 8:
            skipped += 1
            continue
        existing = conn.execute("SELECT id, name FROM contacts WHERE phone=?", (phone,)).fetchone()
        if existing:
            if name and name != phone and existing["name"] == existing["id"] or existing["name"] == phone:
                conn.execute("UPDATE contacts SET name=?, updated_at=? WHERE phone=?", (name, now, phone))
                updated += 1
            else:
                skipped += 1
        else:
            conn.execute(
                "INSERT INTO contacts (phone,name,created_at,updated_at) VALUES(?,?,?,?)",
                (phone, name, now, now),
            )
            inserted += 1
    conn.commit()
    conn.close()
    return {"ok": True, "inserted": inserted, "updated": updated, "skipped": skipped, "total": len(contacts)}


# ── Filas ─────────────────────────────────────────────────────────────────────

def get_queues() -> list[dict]:
    conn = _db()
    rows = conn.execute("SELECT * FROM queues ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Contatos ──────────────────────────────────────────────────────────────────

def get_or_create_contact(phone: str, name: str | None = None) -> dict:
    conn = _db()
    now = _now()
    conn.execute(
        """INSERT INTO contacts (phone,name,created_at,updated_at) VALUES(?,?,?,?)
           ON CONFLICT(phone) DO UPDATE SET
               name=COALESCE(EXCLUDED.name, contacts.name),
               updated_at=EXCLUDED.updated_at""",
        (phone, name or phone, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row)


def get_contacts(search: str = "") -> list[dict]:
    conn = _db()
    if search:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE name LIKE ? OR phone LIKE ? ORDER BY name",
            (f"%{search}%", f"%{search}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM contacts ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Tickets ───────────────────────────────────────────────────────────────────

def get_or_create_ticket(contact_id: int, queue_id: int | None = None) -> dict:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM tickets WHERE contact_id=? AND status != 'closed' ORDER BY id DESC LIMIT 1",
        (contact_id,),
    ).fetchone()
    if row:
        conn.close()
        return dict(row)
    now = _now()
    cur = conn.execute(
        """INSERT INTO tickets (contact_id,queue_id,status,ai_enabled,created_at,updated_at)
           VALUES(?,?,'waiting',1,?,?)""",
        (contact_id, queue_id or 1, now, now),
    )
    tid = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    conn.close()
    return dict(row)


def get_ticket(ticket_id: int) -> Optional[dict]:
    conn = _db()
    row = conn.execute("""
        SELECT t.*, c.name as contact_name, c.phone as contact_phone,
               u.name as agent_name, q.name as queue_name, q.color as queue_color
        FROM tickets t
        JOIN contacts c ON c.id = t.contact_id
        LEFT JOIN users u ON u.id = t.assigned_to
        LEFT JOIN queues q ON q.id = t.queue_id
        WHERE t.id=?
    """, (ticket_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tickets(status: str | None = None, assigned_to: int | None = None) -> list[dict]:
    conn = _db()
    sql = """
        SELECT t.*, c.name as contact_name, c.phone as contact_phone,
               u.name as agent_name, q.name as queue_name, q.color as queue_color
        FROM tickets t
        JOIN contacts c ON c.id = t.contact_id
        LEFT JOIN users u ON u.id = t.assigned_to
        LEFT JOIN queues q ON q.id = t.queue_id
    """
    params: list = []
    wheres: list[str] = []
    if status:
        wheres.append("t.status=?")
        params.append(status)
    if assigned_to is not None:
        wheres.append("t.assigned_to=?")
        params.append(assigned_to)
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY t.last_message_at DESC NULLS LAST, t.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket(ticket_id: int, **kwargs) -> Optional[dict]:
    if not kwargs:
        return get_ticket(ticket_id)
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [ticket_id]
    conn = _db()
    conn.execute(f"UPDATE tickets SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return get_ticket(ticket_id)


def close_ticket(ticket_id: int) -> Optional[dict]:
    return update_ticket(ticket_id, status="closed", unread_count=0)


def get_all_tickets_kanban() -> dict:
    """Retorna tickets agrupados por status para o Kanban."""
    conn = _db()
    rows = conn.execute("""
        SELECT t.*, c.name as contact_name, c.phone as contact_phone,
               u.name as agent_name, q.name as queue_name, q.color as queue_color
        FROM tickets t
        JOIN contacts c ON c.id = t.contact_id
        LEFT JOIN users u ON u.id = t.assigned_to
        LEFT JOIN queues q ON q.id = t.queue_id
        WHERE t.status != 'closed'
        ORDER BY t.last_message_at DESC NULLS LAST
    """).fetchall()
    conn.close()
    result = {"waiting": [], "open": [], "resolved": []}
    for r in rows:
        d = dict(r)
        result.setdefault(d["status"], []).append(d)
    return result


# ── Mensagens ─────────────────────────────────────────────────────────────────

def add_message(
    ticket_id: int,
    body: str,
    from_me: bool = False,
    media_type: str | None = None,
    media_url: str | None = None,
) -> dict:
    now = _now()
    conn = _db()
    cur = conn.execute(
        "INSERT INTO messages (ticket_id,body,from_me,media_type,media_url,created_at) VALUES(?,?,?,?,?,?)",
        (ticket_id, body, int(from_me), media_type, media_url, now),
    )
    msg_id = cur.lastrowid

    # Atualiza último estado do ticket
    unread_delta = 0 if from_me else 1
    conn.execute(
        """UPDATE tickets SET last_message=?, last_message_at=?,
           unread_count = unread_count + ?, updated_at=? WHERE id=?""",
        (body[:120], now, unread_delta, now, ticket_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    conn.close()
    return dict(row)


def get_messages(ticket_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (ticket_id, limit, offset),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def mark_messages_read(ticket_id: int) -> None:
    conn = _db()
    conn.execute("UPDATE tickets SET unread_count=0 WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()


# ── Configurações ─────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    conn = _db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
        (key, value, value),
    )
    conn.commit()
    conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    conn = _db()
    today = datetime.utcnow().date().isoformat()

    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_tickets  = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    open_tickets   = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    waiting        = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='waiting'").fetchone()[0]
    resolved_today = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status IN ('resolved','closed') AND updated_at LIKE ?",
        (f"{today}%",),
    ).fetchone()[0]
    msgs_today = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE created_at LIKE ?", (f"{today}%",)
    ).fetchone()[0]
    ai_active = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE ai_enabled=1 AND status != 'closed'"
    ).fetchone()[0]

    recent = conn.execute("""
        SELECT t.id, c.name as contact_name, c.phone, t.last_message,
               t.last_message_at, t.status, t.unread_count
        FROM tickets t JOIN contacts c ON c.id=t.contact_id
        WHERE t.status != 'closed'
        ORDER BY t.last_message_at DESC NULLS LAST LIMIT 10
    """).fetchall()

    conn.close()
    return {
        "total_contacts":  total_contacts,
        "total_tickets":   total_tickets,
        "open_tickets":    open_tickets,
        "waiting_tickets": waiting,
        "resolved_today":  resolved_today,
        "messages_today":  msgs_today,
        "ai_active":       ai_active,
        "recent_tickets":  [dict(r) for r in recent],
    }


# ── Compatibilidade com agent.py / sessions.py ────────────────────────────────

def load_session(session_id: str) -> list | None:
    """Carrega histórico de mensagens para o agente IA (formato OpenAI/Anthropic)."""
    phone = session_id.replace("wa_", "")
    conn = _db()
    contact = conn.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
    if not contact:
        conn.close()
        return None
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE contact_id=? AND status != 'closed' ORDER BY id DESC LIMIT 1",
        (contact["id"],),
    ).fetchone()
    if not ticket:
        conn.close()
        return None
    rows = conn.execute(
        "SELECT body, from_me FROM messages WHERE ticket_id=? ORDER BY id DESC LIMIT 40",
        (ticket["id"],),
    ).fetchall()
    conn.close()
    history = [
        {"role": "assistant" if r["from_me"] else "user", "content": r["body"]}
        for r in reversed(rows)
    ]
    return history or None


def save_session(session_id: str, messages: list) -> None:
    pass  # mensagens já são salvas em add_message — não precisa de ação aqui


def create_lead(phone: str, name: str | None = None) -> str:
    get_or_create_contact(phone, name)
    return f"wa_{phone}"


def mark_checkout_sent(lead_id: str) -> None:
    phone = lead_id.replace("wa_", "")
    conn = _db()
    contact = conn.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
    if contact:
        conn.execute("UPDATE contacts SET updated_at=? WHERE id=?", (_now(), contact["id"]))
        conn.commit()
    conn.close()
