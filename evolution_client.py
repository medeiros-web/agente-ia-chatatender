"""
Cliente Evolution API — envio de mensagens, indicador de digitação,
download de mídia, registro de webhook. Com retry + backoff exponencial.
"""
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

import config

log = logging.getLogger(__name__)


def _request(
    method: str,
    path: str,
    payload: dict | None = None,
    extra_headers: dict | None = None,
    timeout: int = 15,
    retries: bool = True,
) -> dict:
    """Executa requisição HTTP contra a Evolution API. retry=False para chamadas rápidas."""
    url = f"{config.EVOLUTION_URL}{path}"
    headers = {
        "apikey": config.EVOLUTION_KEY,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    body = json.dumps(payload).encode() if payload is not None else None
    max_attempts = config.MAX_RETRIES if retries else 1
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body_err = e.read().decode(errors="replace")[:300]
            log.warning("HTTP %s para %s (tentativa %d/%d): %s", e.code, path, attempt, max_attempts, body_err)
            if e.code < 500:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning("Erro de rede para %s (tentativa %d/%d): %s", path, attempt, max_attempts, e)
            last_err = e

        if attempt < max_attempts:
            time.sleep(config.RETRY_DELAY * attempt)

    raise RuntimeError(f"Falha após {max_attempts} tentativas para {path}: {last_err}")


# ── Mensagem de texto ─────────────────────────────────────────────────────────

def send_text(phone: str, text: str) -> bool:
    """Envia mensagem de texto. Retorna True em caso de sucesso."""
    try:
        _request("POST", f"/message/sendText/{config.INSTANCE_NAME}", {
            "number": phone,
            "text": text,
        })
        log.info("✓ Mensagem enviada → %s", phone)
        return True
    except Exception as e:
        log.error("✗ Falha ao enviar para %s: %s", phone, e)
        return False


# ── Indicador de digitação ────────────────────────────────────────────────────

def send_typing(phone: str, duration_ms: int = 2000) -> None:
    """Envia indicador 'digitando...' por `duration_ms` milissegundos."""
    if not config.TYPING_ENABLED:
        return
    try:
        _request("POST", f"/chat/sendPresence/{config.INSTANCE_NAME}", {
            "number": phone,
            "presence": "composing",
            "delay": duration_ms,
        }, retries=False, timeout=5)
    except Exception as e:
        log.debug("Typing indicator falhou para %s: %s", phone, e)


# ── Download de mídia ─────────────────────────────────────────────────────────

def download_media(message_id: str) -> Optional[bytes]:
    """Baixa a mídia de uma mensagem pelo ID. Retorna bytes ou None."""
    try:
        result = _request(
            "POST",
            f"/chat/getBase64FromMediaMessage/{config.INSTANCE_NAME}",
            {"message": {"key": {"id": message_id}}, "convertToMp4": False},
            timeout=30,
        )
        import base64
        b64 = result.get("base64", "")
        if b64:
            return base64.b64decode(b64)
    except Exception as e:
        log.warning("Falha ao baixar mídia %s: %s", message_id, e)
    return None


# ── Registro de webhook ───────────────────────────────────────────────────────

def register_webhook(public_url: str) -> bool:
    """Registra/atualiza webhook na instância da Evolution API."""
    try:
        _request("POST", f"/webhook/set/{config.INSTANCE_NAME}", {
            "webhook": {
                "enabled": True,
                "url": f"{public_url}/webhook",
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT"],
            }
        })
        log.info("Webhook registrado: %s/webhook", public_url)
        return True
    except Exception as e:
        log.warning("Não foi possível registrar webhook: %s", e)
        return False


# ── Status da instância ───────────────────────────────────────────────────────

def get_instance_status() -> dict:
    """Retorna status da instância. Sem retry — usado em health check."""
    try:
        return _request(
            "GET", f"/instance/connectionState/{config.INSTANCE_NAME}",
            timeout=3, retries=False,
        )
    except Exception as e:
        log.debug("Falha ao obter status da instancia: %s", e)
        return {"error": str(e)}
