"""
Fila de mensagens com thread pool.
Workers processam transcrição de áudio, IA e envio de respostas
sem bloquear o servidor HTTP.
"""
import time
import queue
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

import config
import evolution_client as evo
import transcriber
from agent import handle_message

log = logging.getLogger(__name__)

# ── Tarefa ────────────────────────────────────────────────────────────────────

@dataclass
class MessageTask:
    phone:        str
    sender_name:  str
    text:         str
    audio_bytes:  Optional[bytes] = None
    audio_mime:   str = "audio/ogg"
    message_id:   str = ""
    ticket_id:    int = 0
    received_at:  float = field(default_factory=time.time)


# ── Métricas ──────────────────────────────────────────────────────────────────

class _Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.processed      = 0
        self.errors         = 0
        self.transcribed    = 0
        self.queue_size     = 0
        self.active_workers = 0

    def inc(self, field: str, n: int = 1):
        with self._lock:
            setattr(self, field, getattr(self, field) + n)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "processed":      self.processed,
                "errors":         self.errors,
                "transcribed":    self.transcribed,
                "queue_size":     self.queue_size,
                "active_workers": self.active_workers,
            }

metrics = _Metrics()

# ── Fila ──────────────────────────────────────────────────────────────────────

_queue: queue.Queue[MessageTask] = queue.Queue(maxsize=config.QUEUE_MAX_SIZE)
_shutdown = threading.Event()

# Referência ao socketio injetada por app.py após inicialização
_socketio = None


def set_socketio(sio) -> None:
    global _socketio
    _socketio = sio


def _emit(event: str, data: dict) -> None:
    if _socketio:
        try:
            _socketio.emit(event, data)
        except Exception:
            pass


def enqueue(task: MessageTask) -> bool:
    try:
        _queue.put_nowait(task)
        metrics.inc("queue_size")
        return True
    except queue.Full:
        log.warning("Fila cheia — descartando mensagem de %s", task.phone)
        return False


# ── Processamento ─────────────────────────────────────────────────────────────

def _process(task: MessageTask) -> None:
    metrics.inc("active_workers")
    try:
        text = task.text

        # Transcrição de áudio
        if not text and task.audio_bytes and transcriber.is_available():
            log.info("Transcrevendo áudio de %s (%d bytes)…", task.phone, len(task.audio_bytes))
            text = transcriber.transcribe(task.audio_bytes, task.audio_mime) or ""
            if text:
                metrics.inc("transcribed")
                log.info("Transcrito: %s", text[:80])
            else:
                log.info("Transcrição vazia — ignorando")
                return

        if not text:
            return

        # Indicador de digitação
        if config.TYPING_ENABLED:
            duration_ms = min(int(config.TYPING_DELAY * 1000), 5000)
            evo.send_typing(task.phone, duration_ms)
            time.sleep(config.TYPING_DELAY)

        # Agente IA
        response = handle_message(task.phone, task.sender_name, text)

        if response:
            evo.send_text(task.phone, response)
            metrics.inc("processed")
            lag = time.time() - task.received_at
            log.info("✓ Respondido %s em %.1fs", task.phone, lag)

            # Emite para o painel em tempo real
            if task.ticket_id:
                from models import add_message as _add_msg, get_ticket
                # add_message já foi chamado em agent.py; só emite o evento
                t = get_ticket(task.ticket_id)
                _emit("ticket_updated", t or {})
        else:
            metrics.inc("processed")

    except Exception as e:
        metrics.inc("errors")
        log.exception("Erro ao processar mensagem de %s: %s", task.phone, e)
    finally:
        metrics.inc("active_workers", -1)
        metrics.inc("queue_size", -1)


# ── Pool ──────────────────────────────────────────────────────────────────────

_threads: list[threading.Thread] = []


def _worker_loop(worker_id: int) -> None:
    log.debug("Worker-%d iniciado", worker_id)
    while not _shutdown.is_set():
        try:
            task = _queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _process(task)
        finally:
            _queue.task_done()
    log.debug("Worker-%d encerrado", worker_id)


def start() -> None:
    _shutdown.clear()
    for i in range(config.WORKER_THREADS):
        t = threading.Thread(target=_worker_loop, args=(i,), daemon=True, name=f"worker-{i}")
        t.start()
        _threads.append(t)
    log.info("Pool de %d workers iniciado", config.WORKER_THREADS)


def stop(timeout: float = 10.0) -> None:
    log.info("Aguardando fila esvaziar…")
    try:
        _queue.join()
    except Exception:
        pass
    _shutdown.set()
    for t in _threads:
        t.join(timeout=timeout)
    log.info("Pool encerrado")
