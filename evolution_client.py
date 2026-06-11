"""
Cliente Evolution GO API — envio de mensagens, indicador de digitação,
download de mídia, registro de webhook.

Modelo de autenticação Evolution GO:
  - Operações globais (/instance/all, /instance/create): apikey = GLOBAL_API_KEY
  - Operações de instância (/send/text, /instance/status, etc.): apikey = INSTANCE_TOKEN
    onde INSTANCE_TOKEN é o campo "token" criado junto com a instância
    (por padrão, definimos token = INSTANCE_NAME na criação)
"""
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

import config

log = logging.getLogger(__name__)

# Token da instância (= config.INSTANCE_NAME por padrão, atualizado após create)
_instance_token: str = ""


def _get_token() -> str:
    """Retorna o token da instância para uso como apikey."""
    return _instance_token or config.INSTANCE_NAME


def _set_token(token: str) -> None:
    global _instance_token
    _instance_token = token


def _request(
    method: str,
    path: str,
    payload: dict | None = None,
    global_auth: bool = False,
    timeout: int = 15,
    retries: bool = True,
) -> dict:
    """
    Executa requisição HTTP contra a Evolution GO API.
    global_auth=True usa GLOBAL_API_KEY (para operações globais).
    global_auth=False usa INSTANCE_TOKEN (para operações de instância).
    """
    url = f"{config.EVOLUTION_URL}{path}"
    api_key = config.EVOLUTION_KEY if global_auth else _get_token()
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }

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
    """Envia mensagem de texto via Evolution GO. Retorna True em caso de sucesso."""
    try:
        _request("POST", "/send/text", {"number": phone, "text": text})
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
        result = _request("POST", "/message/downloadimage", {"messageId": message_id}, timeout=30)
        import base64
        b64 = (
            result.get("data", {}).get("base64")
            or result.get("data", {}).get("media")
            or result.get("base64")
            or result.get("media")
            or ""
        )
        if b64:
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            return base64.b64decode(b64)
    except Exception as e:
        log.warning("Falha ao baixar mídia %s: %s", message_id, e)
    return None


# ── Registro de webhook ───────────────────────────────────────────────────────

def register_webhook(public_url: str) -> bool:
    """Conecta instância e configura webhook na Evolution GO API."""
    try:
        _request("POST", "/instance/connect", {
            "webhookUrl": f"{public_url}/webhook",
            "subscribe": ["MESSAGE", "CONNECTION"],
            "immediate": True,
        })
        log.info("Webhook registrado: %s/webhook", public_url)
        return True
    except Exception as e:
        log.warning("Não foi possível registrar webhook: %s", e)
        return False


# ── Status da instância ───────────────────────────────────────────────────────

def get_instance_status() -> dict:
    """Retorna status da instância normalizado."""
    try:
        result = _request("GET", "/instance/status", timeout=5, retries=False)
        data = result.get("data", result)
        connected = data.get("Connected", False)
        logged_in = data.get("LoggedIn", False)
        if connected and logged_in:
            state = "open"
        elif connected:
            state = "qr"
        else:
            state = "close"
        return {"instance": {"state": state}, "raw": result}
    except Exception as e:
        log.debug("Falha ao obter status da instancia: %s", e)
        return {"instance": {"state": "unknown"}, "error": str(e)}


# ── QR Code ───────────────────────────────────────────────────────────────────

def get_qr_code() -> str:
    """Obtém QR code base64 para scan."""
    try:
        result = _request("GET", "/instance/qr", timeout=10, retries=False)
        data = result.get("data", result)
        return (
            data.get("Qrcode")
            or data.get("qrcode")
            or data.get("base64")
            or data.get("QrCode")
            or ""
        )
    except Exception as e:
        log.debug("Falha ao obter QR code: %s", e)
        return ""


# ── Criação de instância ──────────────────────────────────────────────────────

def create_instance() -> dict:
    """Cria a instância no Evolution GO se não existir."""
    try:
        # Verifica se já existe
        instances = _request("GET", "/instance/all", global_auth=True)
        for inst in instances.get("data", []):
            if inst.get("name") == config.INSTANCE_NAME:
                token = inst.get("token", config.INSTANCE_NAME)
                _set_token(token)
                return {"ok": True, "exists": True, "id": inst["id"], "token": token}
    except Exception:
        pass

    try:
        result = _request("POST", "/instance/create", {
            "name": config.INSTANCE_NAME,
            "token": config.INSTANCE_NAME,
        }, global_auth=True)
        data = result.get("data", result)
        token = data.get("token", config.INSTANCE_NAME)
        _set_token(token)
        return {"ok": True, "exists": False, "data": data, "token": token}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
