import os
import json
import urllib.request
import urllib.error
from pathlib import Path

# Carrega .env automaticamente
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

CHECKOUT_LINK = "https://wa.me/558141012160"

_SYSTEM_PROMPT_DEFAULT = """Você é o assistente virtual do ChatAtender — plataforma de Agente de IA com atendimento 24/7 para WhatsApp.

Seu objetivo é qualificar leads usando a metodologia BANT e conduzir naturalmente à compra.

PRODUTO:
- Nome: ChatAtender
- O que é: Bot de IA para WhatsApp com painel CRM, automação e gestão de conversas
- Benefícios: Atendimento 24/7, 92% menos respostas repetitivas, disparos em massa, follow-up automático, métricas em tempo real
- Para quem: Empresas que usam WhatsApp para vendas — e-commerce, clínicas, agências, lojas
- Investimento: R$ 197/mês ou R$ 1.364/ano (R$ 113,67/mês) — Teste grátis 3 dias
- Site: https://ia.chatatender.com.br

METODOLOGIA BANT — siga esta ordem naturalmente:
1. NEED: Entenda o problema — quantas mensagens recebe por dia? Tem dificuldade em responder rápido?
2. AUTHORITY: É você que decide sobre novas ferramentas na empresa?
3. BUDGET: Apresente o investimento de forma natural após entender a necessidade
4. TIMELINE: Crie urgência genuína — quando precisaria resolver isso?

REGRAS:
- Seja caloroso, direto e confiante — como um consultor de vendas experiente
- Faça UMA pergunta por vez
- Nunca pressione — gere valor antes de falar em preço
- Quando o lead demonstrar interesse real (3+ mensagens ou perguntar sobre preço), envie o link de checkout
- Responda sempre em português brasileiro
- Máximo 3 linhas por mensagem
- Responda APENAS o que foi perguntado — não acrescente informações extras, funcionalidades, preços ou benefícios que não foram solicitados
- Nunca liste múltiplos itens ou funcionalidades de uma vez — apresente um ponto por vez, quando perguntado

INÍCIO: Dê boas-vindas calorosas e faça a primeira pergunta BANT."""

# ── Configurações dinâmicas (recarregáveis via painel) ────────────────────────

_MODELS_DEFAULT = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai":    "gpt-4o-mini",
    "gemini":    "gemini-2.0-flash",
    "grok":      "grok-3-mini",
}

# Estado carregado (atualizado por reload_settings)
AI_PROVIDER   = os.environ.get("AI_PROVIDER", "anthropic").lower()
AI_MODEL      = _MODELS_DEFAULT.get(AI_PROVIDER, "claude-haiku-4-5-20251001")
AI_API_KEY    = ""
GROK_API_KEY  = ""
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT_CUSTOM", "").strip() or _SYSTEM_PROMPT_DEFAULT


def reload_settings() -> None:
    """Recarrega configurações do banco de dados. Chamado após salvar no painel."""
    global AI_PROVIDER, AI_MODEL, AI_API_KEY, GROK_API_KEY, SYSTEM_PROMPT
    try:
        from models import get_setting
    except ImportError:
        return

    provider = get_setting("ai_provider", os.environ.get("AI_PROVIDER", "anthropic")).lower()
    AI_PROVIDER = provider

    key_map = {
        "anthropic": get_setting("anthropic_key", os.environ.get("ANTHROPIC_API_KEY", "")),
        "openai":    get_setting("openai_key",    os.environ.get("OPENAI_API_KEY", "")),
        "gemini":    get_setting("gemini_key",    os.environ.get("GEMINI_API_KEY", "")),
        "grok":      get_setting("grok_key",      os.environ.get("GROK_API_KEY", "")),
    }
    model_map = {
        "anthropic": get_setting("anthropic_model", _MODELS_DEFAULT["anthropic"]),
        "openai":    get_setting("openai_model",    _MODELS_DEFAULT["openai"]),
        "gemini":    get_setting("gemini_model",    _MODELS_DEFAULT["gemini"]),
        "grok":      get_setting("grok_model",      _MODELS_DEFAULT["grok"]),
    }
    AI_API_KEY   = key_map.get(provider, "")
    GROK_API_KEY = key_map.get("grok", "")
    AI_MODEL     = model_map.get(provider, _MODELS_DEFAULT.get(provider, ""))

    custom_prompt = get_setting("system_prompt", "").strip()
    SYSTEM_PROMPT = custom_prompt or os.environ.get("SYSTEM_PROMPT_CUSTOM", "").strip() or _SYSTEM_PROMPT_DEFAULT


# Carrega na inicialização (sem banco ainda, usa env vars)
def _init_from_env():
    global AI_PROVIDER, AI_MODEL, AI_API_KEY, GROK_API_KEY, SYSTEM_PROMPT
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()
    AI_MODEL    = _MODELS_DEFAULT.get(AI_PROVIDER, "claude-haiku-4-5-20251001")
    key_env = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai":    os.environ.get("OPENAI_API_KEY", ""),
        "gemini":    os.environ.get("GEMINI_API_KEY", ""),
        "grok":      os.environ.get("GROK_API_KEY", ""),
    }
    AI_API_KEY   = key_env.get(AI_PROVIDER, "")
    GROK_API_KEY = key_env.get("grok", "")
    SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT_CUSTOM", "").strip() or _SYSTEM_PROMPT_DEFAULT

_init_from_env()


# ── Chamadas IA ───────────────────────────────────────────────────────────────

def call_ai(messages: list, max_tokens: int = 512) -> str:
    if AI_PROVIDER == "openai":
        return _call_openai(messages, max_tokens)
    elif AI_PROVIDER == "gemini":
        return _call_gemini(messages, max_tokens)
    elif AI_PROVIDER == "grok":
        return _call_grok(messages, max_tokens)
    else:
        return _call_anthropic(messages, max_tokens)


def _call_anthropic(messages, max_tokens):
    url = "https://api.anthropic.com/v1/messages"
    data = {"model": AI_MODEL, "max_tokens": max_tokens, "system": SYSTEM_PROMPT, "messages": messages}
    headers = {"x-api-key": AI_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["content"][0]["text"]
    except urllib.error.HTTPError as e:
        return f"Erro Anthropic ({e.code}): {e.read().decode()[:200]}"


def _call_openai(messages, max_tokens):
    url = "https://api.openai.com/v1/chat/completions"
    data = {"model": AI_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "max_completion_tokens": max_tokens, "temperature": 0.7}
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"Erro OpenAI ({e.code}): {e.read().decode()[:200]}"


def _call_gemini(messages, max_tokens):
    url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={AI_API_KEY}"
    data = {"model": AI_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "max_completion_tokens": max_tokens}
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"Erro Gemini ({e.code}): {e.read().decode()[:200]}"


def _call_grok(messages, max_tokens):
    """xAI Grok — API compatível com OpenAI."""
    key = GROK_API_KEY or AI_API_KEY
    url = "https://api.x.ai/v1/chat/completions"
    data = {"model": AI_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "max_tokens": max_tokens, "temperature": 0.7}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"Erro Grok ({e.code}): {e.read().decode()[:200]}"


def is_purchase_intent(message: str, conversation: list = None) -> bool:
    kws = ["quero", "compra", "valor", "preço", "quanto", "custa", "funciona",
           "começar", "assinar", "plano", "contratar"]
    if any(kw in message.lower() for kw in kws):
        return True
    if conversation and len(conversation) >= 6:
        return True
    return False


def format_checkout_message() -> str:
    return f"Perfeito! Vou te enviar o acesso agora mesmo 👇\n\n{CHECKOUT_LINK}\n\nQualquer dúvida após a contratação, estou aqui! 😊"
