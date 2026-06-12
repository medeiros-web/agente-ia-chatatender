"""
Cliente dual-mode: Evolution GO (Go) e Evolution API v1/v2 (TypeScript).

Evolution GO endpoints  → /instance/status, /instance/qr, /send/text, etc.
Evolution API endpoints → /instance/connectionState/{i}, /message/sendText/{i}, etc.

A versão ativa é lida do banco (evo_version) e pode ser "evolution-go" ou "evolution-api".
"""
import json
import time
import base64
import logging
import urllib.request
import urllib.error
from typing import Optional

import config

log = logging.getLogger(__name__)

# ── Estado dinâmico (sobrescrito por reload_settings) ──────────────────────────
_evo_url:      str = ""
_evo_key:      str = ""
_evo_instance: str = ""
_evo_version:  str = "evolution-api"
_inst_token:   str = ""   # Evolution GO: token da instância (pode diferir do INSTANCE_NAME)


# ── Helpers de estado ──────────────────────────────────────────────────────────

def reload_settings() -> None:
    """Lê configurações do banco de dados e atualiza o estado local."""
    global _evo_url, _evo_key, _evo_instance, _evo_version, _inst_token
    try:
        import models
        _evo_url      = models.get_setting("evo_url",      config.EVOLUTION_URL).rstrip("/")
        _evo_key      = models.get_setting("evo_key",      config.EVOLUTION_KEY)
        _evo_instance = models.get_setting("evo_instance", config.INSTANCE_NAME)
        _evo_version  = models.get_setting("evo_version",  "evolution-api")
        log.debug("evo reload: url=%s inst=%s ver=%s", _evo_url, _evo_instance, _evo_version)
    except Exception as e:
        log.debug("reload_settings: usando env vars (%s)", e)
        _evo_url      = config.EVOLUTION_URL.rstrip("/")
        _evo_key      = config.EVOLUTION_KEY
        _evo_instance = config.INSTANCE_NAME
        _evo_version  = "evolution-api"


def _url() -> str:
    return _evo_url or config.EVOLUTION_URL.rstrip("/")

def _key() -> str:
    return _evo_key or config.EVOLUTION_KEY

def _inst() -> str:
    return _evo_instance or config.INSTANCE_NAME

def _ver() -> str:
    return _evo_version or "evolution-api"

def _is_go() -> bool:
    return _ver() == "evolution-go"

def _token() -> str:
    """Evolution GO usa token da instância como apikey para ops de instância."""
    return _inst_token or _inst()


# ── HTTP request helper ────────────────────────────────────────────────────────

def _req(
    method: str,
    path: str,
    payload: dict | None = None,
    global_auth: bool = False,
    timeout: int = 15,
    retries: bool = True,
) -> dict:
    """
    Executa requisição.
    Evolution GO:  ops globais usam _key(), ops de instância usam _token().
    Evolution API: sempre usa _key().
    """
    url     = f"{_url()}{path}"
    api_key = _key() if (global_auth or not _is_go()) else _token()
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body    = json.dumps(payload).encode() if payload is not None else None

    attempts = config.MAX_RETRIES if retries else 1
    last_err: Exception | None = None

    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")[:300]
            log.warning("HTTP %s %s (t=%d/%d): %s", e.code, path, attempt, attempts, err_body)
            if e.code < 500:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning("Net error %s (t=%d/%d): %s", path, attempt, attempts, e)
            last_err = e
        if attempt < attempts:
            time.sleep(config.RETRY_DELAY * attempt)

    raise RuntimeError(f"Falha após {attempts} tentativas para {path}: {last_err}")


def _extract(result: dict, *keys) -> str:
    """Extrai o primeiro valor não-vazio de uma lista de chaves aninhadas."""
    data = result.get("data", result)
    for k in keys:
        v = data.get(k) or result.get(k)
        if v:
            return str(v)
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS DA INSTÂNCIA
# ═══════════════════════════════════════════════════════════════════════════════

def get_instance_status() -> dict:
    """Retorna {'instance': {'state': 'open'|'qr'|'close'|'unknown'}}."""
    try:
        if _is_go():
            # Evolution GO: GET /instance/status
            r    = _req("GET", "/instance/status", timeout=6, retries=False)
            data = r.get("data", r)
            connected = data.get("Connected", data.get("connected", False))
            logged_in = data.get("LoggedIn",  data.get("loggedIn",  False))
            state = "open" if (connected and logged_in) else ("qr" if connected else "close")
        else:
            # Evolution API: GET /instance/connectionState/{instance}
            r    = _req("GET", f"/instance/connectionState/{_inst()}", timeout=6, retries=False)
            raw  = r.get("instance", r.get("state", r))
            if isinstance(raw, dict):
                s = raw.get("state", "close")
            else:
                s = str(raw)
            state = "open" if s in ("open", "connected") else ("qr" if s in ("qr", "connecting") else "close")
        return {"instance": {"state": state}, "raw": r}
    except Exception as e:
        log.debug("get_instance_status: %s", e)
        return {"instance": {"state": "unknown"}, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  QR CODE
# ═══════════════════════════════════════════════════════════════════════════════

def get_qr_code() -> str:
    """Retorna QR code como data:image/png;base64,... ou string vazia."""
    try:
        if _is_go():
            # Evolution GO: GET /instance/qr
            r = _req("GET", "/instance/qr", timeout=12, retries=False)
        else:
            # Evolution API: GET /instance/connect/{instance}
            r = _req("GET", f"/instance/connect/{_inst()}", timeout=12, retries=False)

        data = r.get("data", r)
        qr = (
            data.get("Qrcode") or data.get("qrcode") or
            data.get("base64") or data.get("QrCode") or
            data.get("code")   or r.get("qrcode")    or
            r.get("base64")    or r.get("code")       or ""
        )
        if qr and not qr.startswith("data:"):
            qr = f"data:image/png;base64,{qr}"
        return qr
    except Exception as e:
        log.debug("get_qr_code: %s", e)
        return ""


def connect_instance() -> dict:
    """Inicia/reinicia sessão para gerar novo QR code."""
    try:
        if _is_go():
            payload: dict = {"subscribe": ["MESSAGE", "CONNECTION"]}
            if config.WEBHOOK_PUBLIC_URL:
                payload["webhookUrl"] = f"{config.WEBHOOK_PUBLIC_URL}/webhook"
            r = _req("POST", "/instance/connect", payload, retries=False, timeout=12)
        else:
            # Faz logout para forçar novo QR
            try:
                _req("DELETE", f"/instance/logout/{_inst()}", retries=False, timeout=8)
            except Exception:
                pass
            r = _req("GET", f"/instance/connect/{_inst()}", retries=False, timeout=12)
        return {"ok": True, "data": r}
    except Exception as e:
        log.warning("connect_instance: %s", e)
        return {"ok": False, "error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════════════════════
#  ENVIO DE MENSAGEM
# ═══════════════════════════════════════════════════════════════════════════════

def send_text(phone: str, text: str) -> bool:
    """Envia mensagem de texto. Retorna True em caso de sucesso."""
    try:
        if _is_go():
            # Evolution GO: POST /send/text
            _req("POST", "/send/text", {"number": phone, "text": text})
        else:
            # Evolution API: POST /message/sendText/{instance}
            _req("POST", f"/message/sendText/{_inst()}", {
                "number": phone,
                "textMessage": {"text": text},
            })
        log.info("✓ Mensagem enviada → %s", phone)
        return True
    except Exception as e:
        log.error("✗ Falha envio → %s: %s", phone, e)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  INDICADOR DE DIGITAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def send_typing(phone: str, duration_ms: int = 2000) -> None:
    if not config.TYPING_ENABLED:
        return
    try:
        if _is_go():
            # Evolution GO: POST /message/presence
            _req("POST", "/message/presence", {
                "number": phone, "presence": "composing", "delay": duration_ms,
            }, retries=False, timeout=5)
        else:
            # Evolution API: POST /chat/presence/{instance}
            _req("POST", f"/chat/presence/{_inst()}", {
                "number": phone, "options": {"presence": "composing", "delay": duration_ms},
            }, retries=False, timeout=5)
    except Exception as e:
        log.debug("send_typing %s: %s", phone, e)


# ═══════════════════════════════════════════════════════════════════════════════
#  DOWNLOAD DE MÍDIA
# ═══════════════════════════════════════════════════════════════════════════════

def download_media(message_id: str) -> Optional[bytes]:
    """Baixa mídia pelo ID da mensagem. Retorna bytes ou None."""
    try:
        if _is_go():
            r = _req("POST", "/message/downloadimage", {"messageId": message_id}, timeout=30)
        else:
            r = _req("POST", f"/chat/getBase64FromMediaMessage/{_inst()}", {
                "message": {"key": {"id": message_id}}, "convertToMp4": False,
            }, timeout=30)

        b64 = (
            r.get("data", {}).get("base64") or r.get("data", {}).get("media") or
            r.get("base64") or r.get("media") or ""
        )
        if b64:
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            return base64.b64decode(b64)
    except Exception as e:
        log.warning("download_media %s: %s", message_id, e)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

def register_webhook(public_url: str) -> bool:
    """Registra/atualiza URL de webhook na instância."""
    try:
        if _is_go():
            _req("POST", "/instance/connect", {
                "webhookUrl": f"{public_url}/webhook",
                "subscribe": ["MESSAGE", "CONNECTION"],
                "immediate": True,
            })
        else:
            _req("POST", f"/webhook/set/{_inst()}", {
                "url": f"{public_url}/webhook",
                "webhook_by_events": False,
                "webhook_base64": False,
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            })
        log.info("Webhook registrado: %s/webhook", public_url)
        return True
    except Exception as e:
        log.warning("register_webhook: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  CRIAÇÃO / LISTAGEM DE INSTÂNCIA
# ═══════════════════════════════════════════════════════════════════════════════

def create_instance() -> dict:
    """Cria a instância se não existir. Retorna {ok, exists, token}."""
    global _inst_token
    inst_name = _inst()

    # Verifica se já existe
    try:
        if _is_go():
            r = _req("GET", "/instance/all", global_auth=True)
            for i in r.get("data", []):
                if i.get("name") == inst_name:
                    tok = i.get("token", inst_name)
                    _inst_token = tok
                    return {"ok": True, "exists": True, "token": tok}
        else:
            r = _req("GET", "/instance/fetchInstances", global_auth=True)
            lst = r if isinstance(r, list) else r.get("data", [])
            for i in lst:
                iname = i.get("instance", {}).get("instanceName") or i.get("instanceName", "")
                if iname == inst_name:
                    return {"ok": True, "exists": True}
    except Exception:
        pass

    # Cria
    try:
        if _is_go():
            r    = _req("POST", "/instance/create", {"name": inst_name, "token": inst_name}, global_auth=True)
            data = r.get("data", r)
            tok  = data.get("token", inst_name)
            _inst_token = tok
            return {"ok": True, "exists": False, "token": tok, "data": data}
        else:
            r    = _req("POST", "/instance/create", {
                "instanceName": inst_name, "token": inst_name,
                "qrcode": True, "integration": "WHATSAPP-BAILEYS",
            }, global_auth=True)
            data = r.get("data", r)
            return {"ok": True, "exists": False, "data": data}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def list_instances() -> list[dict]:
    """Lista todas as instâncias disponíveis."""
    try:
        if _is_go():
            r = _req("GET", "/instance/all", global_auth=True)
            return r.get("data", [])
        else:
            r = _req("GET", "/instance/fetchInstances", global_auth=True)
            lst = r if isinstance(r, list) else r.get("data", [])
            return [
                {"name": i.get("instance", {}).get("instanceName") or i.get("instanceName"),
                 "state": i.get("instance", {}).get("connectionStatus") or i.get("connectionStatus")}
                for i in lst
            ]
    except Exception as e:
        log.warning("list_instances: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTATOS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_contacts() -> list[dict]:
    """Busca contatos da instância WhatsApp. Retorna [{phone, name}]."""
    try:
        if _is_go():
            r    = _req("GET", "/contacts/all", timeout=25, retries=False)
            items = r.get("data", r) if isinstance(r.get("data", r), list) else []
        else:
            r    = _req("POST", f"/chat/findContacts/{_inst()}", payload={}, timeout=25, retries=False)
            items = r if isinstance(r, list) else r.get("data", [])

        contacts = []
        for item in items:
            phone = str(
                item.get("id") or item.get("phone") or item.get("Phone") or
                item.get("jid") or item.get("remoteJid") or ""
            ).replace("@s.whatsapp.net", "").replace("@c.us", "").replace("+", "").strip()
            if not phone or "@" in phone or len(phone) < 8:
                continue
            name = str(
                item.get("pushName") or item.get("PushName") or item.get("name") or
                item.get("Name") or item.get("verifiedName") or phone
            ).strip()
            contacts.append({"phone": phone, "name": name or phone})
        return contacts
    except Exception as e:
        log.warning("fetch_contacts: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  TESTE DE CONEXÃO
# ═══════════════════════════════════════════════════════════════════════════════

def test_connection() -> dict:
    """Testa conectividade e retorna status detalhado."""
    status = get_instance_status()
    state  = status.get("instance", {}).get("state", "unknown")
    return {
        "ok":       state != "unknown",
        "state":    state,
        "version":  _ver(),
        "url":      _url(),
        "instance": _inst(),
        "connected": state == "open",
    }
