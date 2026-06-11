"""
Configuração centralizada — carrega .env e valida variáveis obrigatórias.
Importar este módulo antes de qualquer outro garante que os env vars
estejam disponíveis em todo o projeto.
"""
import os
import sys
import logging
from pathlib import Path

# ── Carrega .env ──────────────────────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


def _bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


# ── IA ────────────────────────────────────────────────────────────────────────
AI_PROVIDER       = os.environ.get("AI_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")

# ── Evolution API ─────────────────────────────────────────────────────────────
EVOLUTION_URL  = os.environ.get("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_KEY  = os.environ.get("EVOLUTION_API_KEY", "")
INSTANCE_NAME  = os.environ.get("INSTANCE_NAME", "meu-agente")

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_PORT       = _int("WEBHOOK_PORT", 5000)
WEBHOOK_SECRET     = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_PUBLIC_URL = os.environ.get("WEBHOOK_PUBLIC_URL", "").rstrip("/")

# ── Funcionalidades ───────────────────────────────────────────────────────────
AUDIO_TRANSCRIPTION = _bool("AUDIO_TRANSCRIPTION", True)
TYPING_ENABLED      = _bool("TYPING_ENABLED", True)
TYPING_DELAY        = _float("TYPING_DELAY", 1.5)   # segundos de simulação de digitação

# ── Workers / Fila ────────────────────────────────────────────────────────────
WORKER_THREADS      = _int("WORKER_THREADS", 4)
QUEUE_MAX_SIZE      = _int("QUEUE_MAX_SIZE", 200)
MAX_RETRIES         = _int("MAX_RETRIES", 3)
RETRY_DELAY         = _float("RETRY_DELAY", 2.0)    # segundos entre tentativas

# ── Sessões ───────────────────────────────────────────────────────────────────
SESSION_TTL         = _int("SESSION_TTL", 1800)      # 30 min de inatividade

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_ENABLED   = _bool("DASHBOARD_ENABLED", True)
DASHBOARD_USER      = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS      = os.environ.get("DASHBOARD_PASS", "chatatender")


def validate() -> list[str]:
    """Retorna lista de erros de configuração. Lista vazia = tudo OK."""
    errors = []
    if not EVOLUTION_KEY:
        errors.append("EVOLUTION_API_KEY não configurada")
    ai_keys = {"anthropic": ANTHROPIC_API_KEY, "openai": OPENAI_API_KEY, "gemini": GEMINI_API_KEY}
    if not ai_keys.get(AI_PROVIDER, ""):
        errors.append(f"API key para provedor '{AI_PROVIDER}' não configurada")
    if AUDIO_TRANSCRIPTION and AI_PROVIDER != "openai" and not OPENAI_API_KEY:
        errors.append(
            "AUDIO_TRANSCRIPTION=true mas OPENAI_API_KEY está vazia "
            "(Whisper requer chave OpenAI). Defina AUDIO_TRANSCRIPTION=false para desativar."
        )
    return errors


def validate_or_exit(logger: logging.Logger | None = None) -> None:
    errors = validate()
    if errors:
        msg = "Erros de configuração:\n" + "\n".join(f"  • {e}" for e in errors)
        if logger:
            logger.error(msg)
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
