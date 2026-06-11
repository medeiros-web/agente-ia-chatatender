"""
Camada de acesso ao SQLite — sessões, leads, mensagens e stats.
"""
import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime

# Importa config se disponível (pode não estar na primeira importação do DB)
try:
    import config as _cfg
    _SESSION_TTL = _cfg.SESSION_TTL
except Exception:
    _SESSION_TTL = 1800

import os as _os
DB_PATH = Path(_os.environ.get("DB_PATH", str(Path.home() / "meu-agente" / "dados.sqlite")))


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # escrita concorrente segura
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id           TEXT PRIMARY KEY,
        messages_json TEXT NOT NULL,
        last_activity INTEGER NOT NULL,
        created_at   TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS leads (
        id           TEXT PRIMARY KEY,
        name         TEXT,
        phone        TEXT UNIQUE,
        source       TEXT,
        sent_checkout INTEGER DEFAULT 0,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id  TEXT NOT NULL,
        role     TEXT NOT NULL,
        content  TEXT NOT NULL,
        ts       INTEGER NOT NULL
    )""")
    # Índices para queries do dashboard
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts      ON messages(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_lead_id ON messages(lead_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_leads_created    ON leads(created_at)")
    conn.commit()
    conn.close()


# ── Sessões ───────────────────────────────────────────────────────────────────

def load_session(session_id: str) -> list | None:
    conn = _db()
    row = conn.execute(
        "SELECT messages_json, last_activity FROM sessions WHERE id=?",
        (session_id,)
    ).fetchone()
    conn.close()
    if not row or time.time() - row["last_activity"] > _SESSION_TTL:
        return None
    return json.loads(row["messages_json"])


def save_session(session_id: str, messages: list) -> None:
    conn = _db()
    now = int(time.time())
    conn.execute(
        """INSERT INTO sessions (id, messages_json, last_activity, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET messages_json=?, last_activity=?""",
        (session_id, json.dumps(messages), now, datetime.now().isoformat(),
         json.dumps(messages), now)
    )
    conn.commit()
    conn.close()


# ── Leads ─────────────────────────────────────────────────────────────────────

def create_lead(phone: str, name: str | None = None) -> str:
    conn = _db()
    now = datetime.now().isoformat()
    lead_id = f"wa_{phone}"
    conn.execute(
        """INSERT INTO leads (id, phone, name, source, created_at, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=COALESCE(?,name), updated_at=?""",
        (lead_id, phone, name, "whatsapp", now, now, name, now)
    )
    conn.commit()
    conn.close()
    return lead_id


# ── Mensagens ─────────────────────────────────────────────────────────────────

def add_message(lead_id: str, role: str, content: str) -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO messages (lead_id, role, content, ts) VALUES (?,?,?,?)",
        (lead_id, role, content, int(time.time()))
    )
    conn.commit()
    conn.close()


def mark_checkout_sent(lead_id: str) -> None:
    conn = _db()
    conn.execute(
        "UPDATE leads SET sent_checkout=1, updated_at=? WHERE id=?",
        (datetime.now().isoformat(), lead_id)
    )
    conn.commit()
    conn.close()


# ── Stats para dashboard ──────────────────────────────────────────────────────

def get_message_history(lead_id: str, limit: int = 50) -> list[dict]:
    """Retorna histórico de mensagens de um lead."""
    conn = _db()
    rows = conn.execute(
        "SELECT role, content, ts FROM messages WHERE lead_id=? ORDER BY ts DESC LIMIT ?",
        (lead_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"], "ts": r["ts"]} for r in reversed(rows)]


def get_lead(lead_id: str) -> dict | None:
    conn = _db()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
