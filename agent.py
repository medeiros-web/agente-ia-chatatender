#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from agent_core import call_ai, is_purchase_intent, format_checkout_message
from models import (
    get_or_create_contact, get_or_create_ticket,
    load_session, save_session, add_message, mark_checkout_sent,
    update_ticket,
)

TRIGGER_KEYWORDS = [
    "chatatender", "atendimento", "bot", "whatsapp", "agente", "ia",
    "dúvida", "informação", "quero", "interesse", "plano", "preço",
    "quanto", "custa", "contratar", "teste", "gratis", "grátis",
    "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "hello", "hi",
]


def is_trigger(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in TRIGGER_KEYWORDS)


def handle_message(phone: str, sender_name: str, text: str) -> str | None:
    contact = get_or_create_contact(phone, sender_name)
    ticket  = get_or_create_ticket(contact["id"])
    tid     = ticket["id"]
    lead_id = f"wa_{phone}"

    messages = load_session(lead_id) or []

    # Só responde se há sessão ativa ou mensagem de trigger
    if not messages and not is_trigger(text):
        return None

    messages.append({"role": "user", "content": text})
    # A mensagem já foi salva pelo webhook — não duplica aqui

    response = call_ai(messages)

    messages.append({"role": "assistant", "content": response})
    # Salva resposta do assistente no banco via models
    add_message(tid, response, from_me=True)

    # Abre o ticket se estava aguardando
    if ticket["status"] == "waiting":
        update_ticket(tid, status="open")

    if is_purchase_intent(text, messages) and len(messages) >= 4:
        checkout = f"\n\n{format_checkout_message()}"
        response += checkout
        mark_checkout_sent(lead_id)

    return response


if __name__ == "__main__":
    from models import init_db
    init_db()
    print("Agente ChatAtender OK")
    r = handle_message("5500000000000", "Teste", "Quero saber sobre o ChatAtender")
    print(f"Resposta: {r[:200] if r else 'NENHUMA (trigger não ativado)'}")
