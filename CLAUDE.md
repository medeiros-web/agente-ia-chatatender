# CLAUDE.md — ChatAtender Agent

Agente de vendas IA para WhatsApp usando Evolution API.

## Estrutura do Projeto

```
webhook_server.py   — Servidor HTTP principal (único ponto de entrada)
config.py           — Todas as configurações lidas do .env
evolution_client.py — Cliente Evolution API: send_text, send_typing, download_media
transcriber.py      — Transcrição de áudio via OpenAI Whisper
worker.py           — Fila com thread pool (4 workers por padrão)
dashboard.py        — HTML do painel de monitoramento + stats do SQLite
agent.py            — handle_message: orquestra sessão + resposta IA
agent_core.py       — Chamadas à IA (Anthropic/OpenAI/Gemini) + SYSTEM_PROMPT
sessions.py         — SQLite: tabelas sessions, leads, messages
dados.sqlite        — Banco de dados (criado em ~/meu-agente/)
webhook.log         — Log de execução
```

## Como Rodar

```powershell
python webhook_server.py
```

## Fluxo de uma Mensagem

1. Evolution API → `POST /webhook`
2. `process_webhook()` em webhook_server.py:
   - Filtra fromMe, grupos, broadcasts
   - Deduplica por message ID (LRU cache de 2000 IDs)
   - Extrai texto ou detecta áudio
3. `worker.enqueue(MessageTask)` — resposta imediata 200 ao webhook
4. Worker thread:
   - Se áudio + AUDIO_TRANSCRIPTION=true → `transcriber.transcribe()`
   - `evolution_client.send_typing()` + sleep TYPING_DELAY
   - `agent.handle_message(phone, name, text)`
   - `evolution_client.send_text(phone, response)` com retry

## Endpoints

- `POST /webhook` — recebe eventos Evolution API
- `GET /health` — status + worker metrics + instância Evolution
- `GET /metrics` — só worker metrics (JSON)
- `GET /dashboard` — painel HTML (Basic Auth)
- `GET /dashboard/data` — stats JSON (Basic Auth)

## Configuração (.env)

- `AI_PROVIDER` — anthropic | openai | gemini
- `EVOLUTION_API_KEY` + `EVOLUTION_URL` + `INSTANCE_NAME`
- `WEBHOOK_PORT` (5000) + `WEBHOOK_PUBLIC_URL` (para registro automático)
- `AUDIO_TRANSCRIPTION=true` requer `OPENAI_API_KEY` (Whisper)
- `TYPING_ENABLED=true` + `TYPING_DELAY=1.5` (segundos)
- `WORKER_THREADS=4`, `MAX_RETRIES=3`, `SESSION_TTL=1800`
- `DASHBOARD_USER=admin` + `DASHBOARD_PASS=chatatender`

## Banco de Dados

SQLite em `~/meu-agente/dados.sqlite` (WAL mode para concorrência).
- `sessions` — histórico da conversa por lead_id, TTL=SESSION_TTL
- `leads` — phone, name, sent_checkout
- `messages` — todos os turnos da conversa

## Dependências

Apenas Python 3.11+ stdlib. Nenhum pacote externo obrigatório.

## Evolution API

O endpoint de typing usa o payload: `{"number": "...", "presence": "composing", "delay": ms}`
O endpoint de envio de texto: `POST /message/sendText/{instance}` com `{"number": "...", "text": "..."}`
O download de mídia: `POST /chat/getBase64FromMediaMessage/{instance}` com `{"message": {"key": {"id": "..."}}, "convertToMp4": false}`
