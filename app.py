#!/usr/bin/env python3
"""
ChatAtender — Servidor principal Flask + Socket.IO.
Multi-atendimento WhatsApp com Kanban, IA, QR Code e dashboard em tempo real.
"""
import sys
import json
import base64
import logging
import threading
import time
import urllib.request
import urllib.error
from collections import OrderedDict
from functools import wraps
from pathlib import Path

# config DEVE ser primeiro (carrega .env)
import config

from flask import Flask, request, jsonify, session, send_from_directory, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room

import models
import evolution_client as evo
import worker
from agent_core import call_ai, SYSTEM_PROMPT

# ── Logging ───────────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("webhook.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── App Flask ─────────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.secret_key = config.WEBHOOK_SECRET or "chatatender_secret_key_2024"
app.config["SESSION_COOKIE_HTTPONLY"] = True

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ── Deduplicação de mensagens ─────────────────────────────────────────────────
_seen: OrderedDict[str, float] = OrderedDict()
_seen_lock = threading.Lock()


def _is_duplicate(msg_id: str) -> bool:
    if not msg_id:
        return False
    with _seen_lock:
        if msg_id in _seen:
            return True
        _seen[msg_id] = time.time()
        if len(_seen) > 2000:
            _seen.popitem(last=False)
    return False


# ── Auth helper ───────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── QR Code poller ────────────────────────────────────────────────────────────
_qr_thread_started = False


def _qr_poller():
    """Verifica status e QR code da Evolution GO em loop."""
    last_qr = ""
    while True:
        try:
            status = evo.get_instance_status()
            state = (status.get("instance") or {}).get("state", "unknown")
            socketio.emit("connection_status", {"state": state}, namespace="/")

            if state in ("connecting", "close", "qr", "unknown"):
                qr_b64 = evo.get_qr_code()
                if qr_b64 and qr_b64 != last_qr:
                    last_qr = qr_b64
                    socketio.emit("qrcode", {"qrcode": qr_b64}, namespace="/")
                    try:
                        img_data = qr_b64.split(",")[-1]
                        img = base64.b64decode(img_data)
                        (Path(__file__).parent / "qrcode.png").write_bytes(img)
                    except Exception:
                        pass
            elif state == "open":
                last_qr = ""
        except Exception as e:
            log.debug("QR poller error: %s", e)
        time.sleep(8)


def start_qr_poller():
    global _qr_thread_started
    if not _qr_thread_started:
        _qr_thread_started = True
        t = threading.Thread(target=_qr_poller, daemon=True, name="qr-poller")
        t.start()


# ── Webhook Evolution API ─────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    if config.WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {config.WEBHOOK_SECRET}":
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    try:
        _process_webhook(data)
    except Exception as e:
        log.exception("Erro webhook: %s", e)
    return jsonify({"ok": True})


def _process_webhook(data: dict) -> None:
    event = data.get("event", "")

    # ── Evolution GO format ────────────────────────────────────────────────────
    if event == "Message":
        msg_data = data.get("data", {})
        info = msg_data.get("Info", {})

        if info.get("IsFromMe"):
            return
        remote_jid = info.get("Chat", "")
        is_group = info.get("IsGroup", False) or "@g.us" in remote_jid
        if "@broadcast" in remote_jid:
            return
        if is_group and models.get_setting("reply_groups", "0") != "1":
            return

        msg_id = info.get("ID", "")
        if _is_duplicate(msg_id):
            return

        phone = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "").split(":")[0]
        if not phone:
            return

        sender_name = info.get("PushName") or phone
        message_obj = msg_data.get("Message", {})
        msg_type = info.get("Type", "text").lower()

        text = (
            message_obj.get("conversation")
            or message_obj.get("extendedTextMessage", {}).get("text")
            or message_obj.get("imageMessage", {}).get("caption")
            or message_obj.get("videoMessage", {}).get("caption")
            or ""
        ).strip()

        # Áudio
        audio_bytes: bytes | None = None
        audio_mime = "audio/ogg"
        is_audio = msg_type in ("audio", "ptt", "voice")
        if is_audio and not text:
            if config.AUDIO_TRANSCRIPTION and msg_id:
                audio_bytes = evo.download_media(msg_id)
                if not audio_bytes:
                    text = "[Mensagem de voz]"
            else:
                text = "[Mensagem de voz — envie texto por favor]"

    # ── Classic Evolution API format ──────────────────────────────────────────
    elif event in ("messages.upsert", "MESSAGES_UPSERT"):
        msg_data = data.get("data", {})
        key = msg_data.get("key", {})

        if key.get("fromMe"):
            return
        remote_jid = key.get("remoteJid", "")
        is_group = "@g.us" in remote_jid
        if "@broadcast" in remote_jid:
            return
        if is_group and models.get_setting("reply_groups", "0") != "1":
            return

        msg_id = key.get("id", "")
        if _is_duplicate(msg_id):
            return

        phone = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")
        if not phone:
            return

        sender_name = msg_data.get("pushName") or phone
        message_obj = msg_data.get("message", {})

        text = (
            message_obj.get("conversation")
            or message_obj.get("extendedTextMessage", {}).get("text")
            or message_obj.get("imageMessage", {}).get("caption")
            or message_obj.get("videoMessage", {}).get("caption")
            or ""
        ).strip()

        audio_bytes: bytes | None = None
        audio_mime = "audio/ogg"
        is_audio = "audioMessage" in message_obj or "pttMessage" in message_obj
        if is_audio and not text:
            audio_info = message_obj.get("audioMessage") or message_obj.get("pttMessage") or {}
            audio_mime = audio_info.get("mimetype", "audio/ogg")
            if config.AUDIO_TRANSCRIPTION and msg_id:
                audio_bytes = evo.download_media(msg_id)
                if not audio_bytes:
                    text = "[Mensagem de voz]"
            else:
                text = "[Mensagem de voz — envie texto por favor]"
    else:
        return

    if not text and not audio_bytes:
        return

    # Cria/encontra contato e ticket
    contact = models.get_or_create_contact(phone, sender_name)
    ticket = models.get_or_create_ticket(contact["id"])
    tid = ticket["id"]

    display_text = text or "[áudio]"
    msg = models.add_message(tid, display_text, from_me=False)
    log.info("↓ #%d %s: %s", tid, phone, display_text[:80])

    # Emite para o painel em tempo real
    ticket_data = models.get_ticket(tid)
    socketio.emit("new_message", {
        "ticket_id": tid,
        "message": msg,
        "ticket": ticket_data,
    }, namespace="/")
    socketio.emit("ticket_updated", ticket_data, namespace="/")

    # Enfileira para processamento IA (se IA ativada no ticket)
    if ticket.get("ai_enabled", 1):
        task = worker.MessageTask(
            phone=phone,
            sender_name=sender_name,
            text=text or "",
            audio_bytes=audio_bytes,
            audio_mime=audio_mime,
            message_id=msg_id,
            ticket_id=tid,
        )
        worker.enqueue(task)


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json() or {}
    user = models.authenticate_user(body.get("email", ""), body.get("password", ""))
    if not user:
        return jsonify({"error": "Credenciais inválidas"}), 401
    session["user_id"]   = user["id"]
    session["user_name"] = user["name"]
    session["user_role"] = user["role"]
    return jsonify({"id": user["id"], "name": user["name"], "role": user["role"]})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
@login_required
def api_me():
    return jsonify({
        "id":   session["user_id"],
        "name": session["user_name"],
        "role": session["user_role"],
    })


# ── Tickets ───────────────────────────────────────────────────────────────────
@app.route("/api/tickets")
@login_required
def api_tickets():
    status = request.args.get("status")
    tickets = models.get_tickets(status=status)
    return jsonify(tickets)


@app.route("/api/tickets/kanban")
@login_required
def api_kanban():
    return jsonify(models.get_all_tickets_kanban())


@app.route("/api/tickets/<int:tid>")
@login_required
def api_ticket(tid):
    t = models.get_ticket(tid)
    if not t:
        return jsonify({"error": "not found"}), 404
    models.mark_messages_read(tid)
    return jsonify(t)


@app.route("/api/tickets/<int:tid>", methods=["PATCH"])
@login_required
def api_update_ticket(tid):
    body = request.get_json() or {}
    allowed = {"status", "assigned_to", "queue_id", "ai_enabled"}
    updates = {k: v for k, v in body.items() if k in allowed}
    ticket = models.update_ticket(tid, **updates)
    if not ticket:
        return jsonify({"error": "not found"}), 404
    socketio.emit("ticket_updated", ticket, namespace="/")
    return jsonify(ticket)


@app.route("/api/tickets/<int:tid>/close", methods=["POST"])
@login_required
def api_close_ticket(tid):
    ticket = models.close_ticket(tid)
    socketio.emit("ticket_updated", ticket, namespace="/")
    return jsonify(ticket)


# ── Mensagens ─────────────────────────────────────────────────────────────────
@app.route("/api/tickets/<int:tid>/messages")
@login_required
def api_messages(tid):
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    msgs = models.get_messages(tid, limit=limit, offset=offset)
    models.mark_messages_read(tid)
    return jsonify(msgs)


@app.route("/api/tickets/<int:tid>/messages", methods=["POST"])
@login_required
def api_send_message(tid):
    body = request.get_json() or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "texto vazio"}), 400

    ticket = models.get_ticket(tid)
    if not ticket:
        return jsonify({"error": "not found"}), 404

    # Envia pelo WhatsApp
    evo.send_text(ticket["contact_phone"], text)

    # Salva no banco
    msg = models.add_message(tid, text, from_me=True)

    # Atualiza status para 'open' se estava waiting
    if ticket["status"] == "waiting":
        updated = models.update_ticket(tid, status="open", assigned_to=session["user_id"])
        socketio.emit("ticket_updated", updated, namespace="/")

    socketio.emit("new_message", {"ticket_id": tid, "message": msg}, namespace="/")
    return jsonify(msg), 201


# ── Contatos ──────────────────────────────────────────────────────────────────
@app.route("/api/contacts")
@login_required
def api_contacts():
    search = request.args.get("q", "")
    return jsonify(models.get_contacts(search=search))


# ── Filas ─────────────────────────────────────────────────────────────────────
@app.route("/api/queues")
@login_required
def api_queues():
    return jsonify(models.get_queues())


# ── Usuários ──────────────────────────────────────────────────────────────────
@app.route("/api/users")
@login_required
def api_users():
    return jsonify(models.get_users())


# ── QR Code ───────────────────────────────────────────────────────────────────
@app.route("/api/qrcode")
@login_required
def api_qrcode():
    """Busca QR code atual da Evolution GO."""
    try:
        qr_b64 = evo.get_qr_code()
        return jsonify({"qrcode": qr_b64, "code": qr_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connection/status")
@login_required
def api_connection_status():
    return jsonify(evo.get_instance_status())


# ── Configurações IA ─────────────────────────────────────────────────────────
@app.route("/api/settings/ai")
@login_required
def api_ai_settings_get():
    """Retorna config atual de IA salva no banco."""
    return jsonify({
        "provider":        models.get_setting("ai_provider",  config.AI_PROVIDER),
        "anthropic_key":   models.get_setting("anthropic_key",  ""),
        "openai_key":      models.get_setting("openai_key",     ""),
        "gemini_key":      models.get_setting("gemini_key",     ""),
        "grok_key":        models.get_setting("grok_key",       ""),
        "anthropic_model": models.get_setting("anthropic_model","claude-haiku-4-5-20251001"),
        "openai_model":    models.get_setting("openai_model",   "gpt-4o-mini"),
        "gemini_model":    models.get_setting("gemini_model",   "gemini-2.0-flash"),
        "grok_model":      models.get_setting("grok_model",     "grok-3-mini"),
        "system_prompt":   models.get_setting("system_prompt",  ""),
    })


@app.route("/api/settings/ai", methods=["POST"])
@login_required
def api_ai_settings_save():
    """Salva config de IA no banco e recarrega o agent_core."""
    body = request.get_json() or {}
    allowed = {
        "provider", "anthropic_key", "openai_key", "gemini_key", "grok_key",
        "anthropic_model", "openai_model", "gemini_model", "grok_model", "system_prompt",
    }
    for k, v in body.items():
        if k in allowed:
            models.set_setting(k, str(v))
    # Recarrega configurações no agent_core sem reiniciar
    import agent_core
    agent_core.reload_settings()
    return jsonify({"ok": True})


# ── Configurações Evolution API ──────────────────────────────────────────────
@app.route("/api/settings/evolution")
@login_required
def api_evo_settings_get():
    return jsonify({
        "url":      models.get_setting("evo_url",      config.EVOLUTION_URL),
        "key":      models.get_setting("evo_key",      config.EVOLUTION_KEY),
        "instance": models.get_setting("evo_instance", config.INSTANCE_NAME),
        "version":  models.get_setting("evo_version",  "evolution-api"),
    })


@app.route("/api/settings/evolution", methods=["POST"])
@login_required
def api_evo_settings_save():
    body = request.get_json() or {}
    for k, v in body.items():
        if k in {"url", "key", "instance", "version"}:
            models.set_setting(f"evo_{k}", str(v))
    return jsonify({"ok": True})


@app.route("/api/settings/general")
@login_required
def api_general_settings_get():
    return jsonify({
        "reply_groups": models.get_setting("reply_groups", "0"),
    })


@app.route("/api/settings/general", methods=["POST"])
@login_required
def api_general_settings_save():
    body = request.get_json() or {}
    if "reply_groups" in body:
        models.set_setting("reply_groups", "1" if body["reply_groups"] else "0")
    return jsonify({"ok": True})


@app.route("/api/settings/evolution/test")
@login_required
def api_evo_test():
    url      = models.get_setting("evo_url",      config.EVOLUTION_URL)
    key      = models.get_setting("evo_key",      config.EVOLUTION_KEY)
    instance = models.get_setting("evo_instance", config.INSTANCE_NAME)
    try:
        req = urllib.request.Request(
            f"{url}/instance/connectionState/{instance}",
            headers={"apikey": key},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            state = (data.get("instance") or {}).get("state", "unknown")
            return jsonify({"connected": True, "state": state})
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)[:120]})


@app.route("/api/settings/evolution/create-instance", methods=["POST"])
@login_required
def api_evo_create_instance():
    result = evo.create_instance()
    if not result.get("ok"):
        return jsonify(result), 400
    # Registra webhook se URL configurada
    if config.WEBHOOK_PUBLIC_URL:
        evo.register_webhook(config.WEBHOOK_PUBLIC_URL)
    return jsonify(result)


# ── Stats / Dashboard ─────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    stats = models.get_stats()
    stats["worker"] = worker.metrics.snapshot()
    return jsonify(stats)


# ── Health ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({
        "status":   "ok",
        "instance": config.INSTANCE_NAME,
        "worker":   worker.metrics.snapshot(),
    })


# ── Frontend SPA ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/<path:path>")
def catch_all(path):
    fp = STATIC_DIR / path
    if fp.exists() and fp.is_file():
        return send_from_directory(str(STATIC_DIR), path)
    return send_from_directory(str(STATIC_DIR), "index.html")


# ── Socket.IO ─────────────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    log.debug("Socket.IO client conectado: %s", request.sid)


@socketio.on("join_ticket")
def on_join_ticket(data):
    tid = str(data.get("ticket_id", ""))
    if tid:
        join_room(f"ticket_{tid}")


@socketio.on("leave_ticket")
def on_leave_ticket(data):
    tid = str(data.get("ticket_id", ""))
    if tid:
        leave_room(f"ticket_{tid}")


# ── Ponto de entrada ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    models.init_db()

    # Carrega config de IA salva no banco (sobrescreve env vars)
    import agent_core
    agent_core.reload_settings()

    if config.WEBHOOK_PUBLIC_URL:
        evo.register_webhook(config.WEBHOOK_PUBLIC_URL)

    worker.start()
    start_qr_poller()

    log.info("=" * 55)
    log.info("  ChatAtender iniciando na porta %s", config.WEBHOOK_PORT)
    log.info("  Acesse: http://localhost:%s", config.WEBHOOK_PORT)
    log.info("  Login: medeirosassessor.adv@gmail.com / Aa213780@")
    log.info("=" * 55)

    socketio.run(
        app,
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
