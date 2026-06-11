"""
Cliente Evolution GO API — envio de mensagens, indicador de digitação,
download de mídia, registro de webhook. Compatível com Evolution GO.
"""
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

import config

log = logging.getLogger(__name__)

# Cache do UUID da instância
_instance_id_cache: str = ""


def _get_instance_id() -> str:
    """Retorna o UUID da instância pelo nome. Cacheia o resultado."""
    global _instance_id_cache
    if _instance_id_cache:
        return _instance_id_cache
    try:
        result = _request_base("GET", "/instance/all", timeout=10, retries=False)
        for inst in result.get("data", []):
            if inst.get("name") == config.INSTANCE_NAME:
                _instance_id_cache = inst["id"]
                log.info("Instance ID encontrado: %s → %s", config.INSTANCE_NAME, _instance_id_cache)
                return _instance_id_cache
    except Exception as e:
        log.debug("Falha ao buscar instanceId: %s", e)
    return ""


def _clear_instance_cache() -> None:
    global _instance_id_cache
    _instance_id_cache = ""


def _request_base(
    method: str,
    path: str,
    payload: dict | None = None,
    extra_headers: dict | None = None,
    timeout: int = 15,
    retries: bool = True,
) -> dict:
    """Requisição HTTP base (sem instanceId automático)."""
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


def _request(
    method: str,
    path: str,
    payload: dict | None = None,
    extra_headers: dict | None = None,
    timeout: int = 15,
    retries: bool = True,
) -> dict:
    """Requisição com instanceId header automático."""
    instance_id = _get_instance_id()
    headers = {}
    if instance_id:
        headers["instanceId"] = instance_id
    if extra_headers:
        headers.update(extra_headers)
    return _request_base(method, path, payload, headers, timeout, retries)


# ── Mensagem de texto ─────────────────────────────────────────────────────────

def send_text(phone: str, text: str) -> bool:
    """Envia mensagem de texto via Evolution GO. Retorna True em caso de sucesso."""
    try:
        _request("POST", "/send/text", {
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
        _request("POST", "/message/presence", {
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
            "/message/downloadimage",
            {"messageId": message_id},
            timeout=30,
        )
        import base64
        b64 = result.get("base64") or result.get("data") or result.get("media") or ""
        if b64:
            # Remove data URI prefix if present
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            return base64.b64decode(b64)
    except Exception as e:
        log.warning("Falha ao baixar mídia %s: %s", message_id, e)
    return None


# ── Registro de webhook ───────────────────────────────────────────────────────

def register_webhook(public_url: str) -> bool:
    """Configura webhook na instância Evolution GO."""
    _clear_instance_cache()
    instance_id = _get_instance_id()
    if not instance_id:
        log.warning("Instância '%s' não encontrada — webhook não registrado", config.INSTANCE_NAME)
        return False
    try:
        _request_base("POST", "/instance/connect", {
            "webhookUrl": f"{public_url}/webhook",
            "subscribe": ["MESSAGE"],
            "immediate": True,
        }, extra_headers={"instanceId": instance_id})
        log.info("Webhook registrado: %s/webhook", public_url)
        return True
    except Exception as e:
        log.warning("Não foi possível registrar webhook: %s", e)
        return False


# ── Status da instância ───────────────────────────────────────────────────────

def get_instance_status() -> dict:
    """Retorna status da instância normalizado para formato clássico."""
    try:
        instance_id = _get_instance_id()
        if not instance_id:
            return {"instance": {"state": "unknown"}}
        result = _request_base(
            "GET", "/instance/status",
            extra_headers={"instanceId": instance_id},
            timeout=5, retries=False,
        )
        connected = result.get("connected", False)
        state = "open" if connected else "close"
        return {"instance": {"state": state}, "raw": result}
    except Exception as e:
        log.debug("Falha ao obter status da instancia: %s", e)
        return {"instance": {"state": "unknown"}, "error": str(e)}


# ── QR Code ───────────────────────────────────────────────────────────────────

def get_qr_code() -> str:
    """Obtém QR code base64 para scan."""
    try:
        instance_id = _get_instance_id()
        if not instance_id:
            return ""
        result = _request_base(
            "GET", "/instance/qr",
            extra_headers={"instanceId": instance_id},
            timeout=10, retries=False,
        )
        return (
            result.get("base64")
            or result.get("qrCode")
            or result.get("qrcode")
            or result.get("code")
            or ""
        )
    except Exception as e:
        log.debug("Falha ao obter QR code: %s", e)
        return ""


# ── Criação de instância ──────────────────────────────────────────────────────

def create_instance() -> dict:
    """Cria a instância no Evolution GO se não existir."""
    _clear_instance_cache()
    existing_id = _get_instance_id()
    if existing_id:
        return {"ok": True, "exists": True, "id": existing_id}
    try:
        result = _request_base("POST", "/instance/create", {
            "name": config.INSTANCE_NAME,
            "token": config.INSTANCE_NAME,
        })
        data = result.get("data", result)
        instance_id = data.get("id", "")
        if instance_id:
            _instance_id_cache_set(instance_id)
        return {"ok": True, "exists": False, "data": data}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _instance_id_cache_set(value: str) -> None:
    global _instance_id_cache
    _instance_id_cache = value
