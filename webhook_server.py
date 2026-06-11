#!/usr/bin/env python3
"""
ChatAtender — Servidor principal.
Recebe webhooks da Evolution API, deduplica, enfileira e responde via agente IA.
Inclui dashboard de monitoramento em /dashboard.
"""
import signal
import sys
import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import OrderedDict

# config DEVE ser importado primeiro (carrega .env)
import config
import worker
import dashboard
import evolution_client as evo
from models import init_db

# ── Logging ───────────────────────────────────────────────────────────────────
# Garante UTF-8 no stdout (Windows cp1252 não suporta alguns chars)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("webhook.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Deduplicação (LRU cache de IDs de mensagens já processados) ───────────────
_SEEN_MAX = 2000
_seen_ids: OrderedDict[str, float] = OrderedDict()
_seen_lock = threading.Lock()


def _is_duplicate(msg_id: str) -> bool:
    if not msg_id:
        return False
    with _seen_lock:
        if msg_id in _seen_ids:
            return True
        _seen_ids[msg_id] = time.time()
        if len(_seen_ids) > _SEEN_MAX:
            _seen_ids.popitem(last=False)
    return False


# ── Processamento do payload ──────────────────────────────────────────────────

def process_webhook(data: dict) -> None:
    event = data.get("event", "")
    if event not in ("messages.upsert", "MESSAGES_UPSERT"):
        return

    msg_data = data.get("data", {})
    key = msg_data.get("key", {})

    # Ignora mensagens próprias e de grupos
    if key.get("fromMe"):
        return
    remote_jid = key.get("remoteJid", "")
    if "@g.us" in remote_jid or "@broadcast" in remote_jid:
        return

    msg_id = key.get("id", "")
    if _is_duplicate(msg_id):
        log.debug("Mensagem duplicada ignorada: %s", msg_id)
        return

    phone = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")
    if not phone:
        return

    sender_name = msg_data.get("pushName") or phone
    message_obj = msg_data.get("message", {})

    # ── Extrai texto ──────────────────────────────────────────────────────
    text = (
        message_obj.get("conversation")
        or message_obj.get("extendedTextMessage", {}).get("text")
        or message_obj.get("imageMessage", {}).get("caption")
        or message_obj.get("videoMessage", {}).get("caption")
        or message_obj.get("documentMessage", {}).get("caption")
        or ""
    ).strip()

    # ── Extrai áudio ──────────────────────────────────────────────────────
    audio_bytes: bytes | None = None
    audio_mime = "audio/ogg"
    is_audio = "audioMessage" in message_obj or "pttMessage" in message_obj

    if is_audio and not text:
        audio_info = message_obj.get("audioMessage") or message_obj.get("pttMessage") or {}
        audio_mime = audio_info.get("mimetype", "audio/ogg")

        if msg_id and config.AUDIO_TRANSCRIPTION:
            audio_bytes = evo.download_media(msg_id)
            if not audio_bytes:
                log.info("Não foi possível baixar áudio de %s — ignorando", phone)
                return
        else:
            # Transcrição desativada: avisa o usuário
            task = worker.MessageTask(
                phone=phone,
                sender_name=sender_name,
                text="[Mensagem de voz — por favor, envie texto]",
                message_id=msg_id,
            )
            worker.enqueue(task)
            return

    if not text and not audio_bytes:
        log.debug("Mensagem sem conteúdo processável de %s", phone)
        return

    log.info("↓ %s (%s): %s", phone, sender_name, (text or "[áudio]")[:80])

    task = worker.MessageTask(
        phone=phone,
        sender_name=sender_name,
        text=text,
        audio_bytes=audio_bytes,
        audio_mime=audio_mime,
        message_id=msg_id,
    )
    worker.enqueue(task)


# ── Handler HTTP ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silencia logs de acesso HTTP

    # ── POST /webhook ─────────────────────────────────────────────────────
    def do_POST(self):
        if not self.path.startswith("/webhook"):
            self._json(404, {"error": "not found"})
            return

        if config.WEBHOOK_SECRET:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {config.WEBHOOK_SECRET}":
                self._json(401, {"error": "unauthorized"})
                return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return

        try:
            process_webhook(data)
        except Exception as e:
            log.exception("Erro ao processar webhook: %s", e)

        self._json(200, {"ok": True})

    # ── GET ───────────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            snap = worker.metrics.snapshot()
            status = evo.get_instance_status()
            self._json(200, {
                "status":   "ok",
                "instance": config.INSTANCE_NAME,
                "worker":   snap,
                "evolution": status,
            })

        elif path == "/metrics":
            self._json(200, worker.metrics.snapshot())

        elif path in ("/dashboard", "/dashboard/"):
            if not config.DASHBOARD_ENABLED:
                self._json(403, {"error": "dashboard disabled"})
                return
            auth = self.headers.get("Authorization")
            if not dashboard.check_auth(auth):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="ChatAtender"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            stats = dashboard.get_stats()
            html  = dashboard.render_html(stats).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif path == "/dashboard/data":
            if not config.DASHBOARD_ENABLED:
                self._json(403, {"error": "dashboard disabled"})
                return
            auth = self.headers.get("Authorization")
            if not dashboard.check_auth(auth):
                self._json(401, {"error": "unauthorized"})
                return
            self._json(200, dashboard.get_stats())

        else:
            self._json(404, {"error": "not found"})

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Entrada ───────────────────────────────────────────────────────────────────

def main():
    config.validate_or_exit(log)
    init_db()
    worker.start()

    if config.WEBHOOK_PUBLIC_URL:
        evo.register_webhook(config.WEBHOOK_PUBLIC_URL)

    server = HTTPServer(("0.0.0.0", config.WEBHOOK_PORT), Handler)
    server.timeout = 5

    sep = "=" * 60
    log.info(sep)
    log.info("  ChatAtender Webhook Server")
    log.info("  Porta       : %d", config.WEBHOOK_PORT)
    log.info("  Instancia   : %s", config.INSTANCE_NAME)
    log.info("  Provedor IA : %s", config.AI_PROVIDER.upper())
    log.info("  Workers     : %d threads", config.WORKER_THREADS)
    log.info("  Transcricao : %s", "ativada" if config.AUDIO_TRANSCRIPTION else "desativada")
    log.info("  Dashboard   : http://localhost:%d/dashboard", config.WEBHOOK_PORT)
    log.info("  Health      : http://localhost:%d/health", config.WEBHOOK_PORT)
    if config.WEBHOOK_PUBLIC_URL:
        log.info("  Webhook URL : %s/webhook", config.WEBHOOK_PUBLIC_URL)
    log.info(sep)

    # Graceful shutdown
    def _shutdown(sig, frame):
        log.info("Sinal %s recebido — encerrando...", sig)
        worker.stop()
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        server.server_close()
        log.info("Servidor encerrado.")


if __name__ == "__main__":
    main()
