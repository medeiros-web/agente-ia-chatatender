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

# Configuração dinâmica (pode ser sobrescrita por reload_settings)
_instance_token: str = ""
_evo_url: str = ""
_evo_key: str = ""
_evo_instance: str = ""
_evo_version: str = "evolution-go"  # "evolution-go" ou "evolution-api"


def reload_settings() -> None:
    """Recarrega URL/key/instance do banco de dados (se disponível)."""
    global _evo_url, _evo_key, _evo_instance, _evo_version, _instance_token
    try:
        import models
        _evo_url      = models.get_setting("evo_url",      config.EVOLUTION_URL).rstrip("/")
        _evo_key      = models.get_setting("evo_key",      config.EVOLUTION_KEY)
        _evo_instance = models.get_setting("evo_instance", config.INSTANCE_NAME)
        _evo_version  = models.get_setting("evo_version",  "evolution-go")
        log.debug("Evolution settings recarregadas: url=%s instance=%s version=%s", _evo_url, _evo_instance, _evo_version)
    except Exception as e:
        log.debug("reload_settings falhou, usando config.py: %s", e)
        _evo_url      = config.EVOLUTION_URL.rstrip("/")
        _evo_key      = config.EVOLUTION_KEY
        _evo_instance = config.INSTANCE_NAME


def _get_url() -> str:
    return _evo_url or config.EVOLUTION_URL.rstrip("/")


def _get_key() -> str:
    return _evo_key or config.EVOLUTION_KEY


def _get_instance() -> str:
    return _evo_instance or config.INSTANCE_NAME


def _get_version() -> str:
    return _evo_version or "evolution-go"


def _get_token() -> str:
    """Retorna o token da instância para uso como apikey."""
    return _instance_token or _get_instance()


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
    Executa requisição HTTP contra a Evolution API.
    global_auth=True usa a chave global; False usa token da instância (Evolution GO)
    ou a chave global (Evolution API v1/v2 usa sempre a mesma key).
    """
    url = f"{_get_url()}{path}"
    if _get_version() == "evolution-go":
        api_key = _get_key() if global_auth else _get_token()
    else:
        api_key = _get_key()
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
    """Conecta instância e configura webhook."""
    try:
        ver = _get_version()
        if ver == "evolution-go":
            _request("POST", "/instance/connect", {
                "webhookUrl": f"{public_url}/webhook",
                "subscribe": ["MESSAGE", "CONNECTION"],
                "immediate": True,
            })
        else:
            inst = _get_instance()
            _request("POST", f"/webhook/set/{inst}", {
                "url": f"{public_url}/webhook",
                "webhook_by_events": False,
                "webhook_base64": False,
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            })
        log.info("Webhook registrado: %s/webhook", public_url)
        return True
    except Exception as e:
        log.warning("Não foi possível registrar webhook: %s", e)
        return False


# ── Status da instância ───────────────────────────────────────────────────────

def get_instance_status() -> dict:
    """Retorna status da instância normalizado para Evolution GO ou Evolution API."""
    try:
        ver = _get_version()
        if ver == "evolution-go":
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
        else:
            # Evolution API clássica
            inst = _get_instance()
            result = _request("GET", f"/instance/connectionState/{inst}", timeout=5, retries=False)
            data = result.get("instance", result)
            state = data.get("state", "close")
            # Normaliza estados da Evolution API
            if state in ("open", "connected"):
                state = "open"
            elif state in ("connecting", "qr"):
                state = "qr"
            else:
                state = "close"
        return {"instance": {"state": state}, "raw": result}
    except Exception as e:
        log.debug("Falha ao obter status da instancia: %s", e)
        return {"instance": {"state": "unknown"}, "error": str(e)}


# ── QR Code ───────────────────────────────────────────────────────────────────

def get_qr_code() -> str:
    """Obtém QR code base64 para scan (Evolution GO ou Evolution API)."""
    try:
        ver = _get_version()
        if ver == "evolution-go":
            result = _request("GET", "/instance/qr", timeout=10, retries=False)
        else:
            inst = _get_instance()
            result = _request("GET", f"/instance/connect/{inst}", timeout=10, retries=False)

        # Extrai base64 de qualquer campo possível
        data = result.get("data", result)
        qr = (
            data.get("Qrcode")
            or data.get("qrcode")
            or data.get("base64")
            or data.get("QrCode")
            or data.get("code")
            or result.get("qrcode")
            or result.get("base64")
            or result.get("code")
            or ""
        )
        # Garante que tem prefixo data:image
        if qr and not qr.startswith("data:"):
            qr = f"data:image/png;base64,{qr}"
        return qr
    except Exception as e:
        log.debug("Falha ao obter QR code: %s", e)
        return ""


def connect_instance() -> dict:
    """Força início de nova sessão / geração de QR code."""
    try:
        ver = _get_version()
        if ver == "evolution-go":
            result = _request("POST", "/instance/connect", {
                "webhookUrl": "",
                "subscribe": ["MESSAGE", "CONNECTION"],
            }, retries=False, timeout=10)
        else:
            inst = _get_instance()
            result = _request("DELETE", f"/instance/logout/{inst}", retries=False, timeout=8)
        return {"ok": True, "data": result}
    except Exception as e:
        log.warning("connect_instance falhou: %s", e)
        return {"ok": False, "error": str(e)[:200]}


# ── Criação de instância ──────────────────────────────────────────────────────

def create_instance() -> dict:
    """Cria a instância no Evolution GO/API se não existir."""
    inst_name = _get_instance()
    ver = _get_version()
    try:
        if ver == "evolution-go":
            instances = _request("GET", "/instance/all", global_auth=True)
            for inst in instances.get("data", []):
                if inst.get("name") == inst_name:
                    token = inst.get("token", inst_name)
                    _set_token(token)
                    return {"ok": True, "exists": True, "id": inst.get("id"), "token": token}
        else:
            # Evolution API: verifica se instância existe via fetchInstances
            try:
                instances = _request("GET", "/instance/fetchInstances", global_auth=True)
                for inst in (instances if isinstance(instances, list) else []):
                    if inst.get("instance", {}).get("instanceName") == inst_name:
                        return {"ok": True, "exists": True}
            except Exception:
                pass
    except Exception:
        pass

    try:
        if ver == "evolution-go":
            result = _request("POST", "/instance/create", {
                "name": inst_name,
                "token": inst_name,
            }, global_auth=True)
        else:
            result = _request("POST", "/instance/create", {
                "instanceName": inst_name,
                "token": inst_name,
                "qrcode": True,
            }, global_auth=True)
        data = result.get("data", result)
        token = data.get("token", inst_name)
        _set_token(token)
        return {"ok": True, "exists": False, "data": data, "token": token}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
