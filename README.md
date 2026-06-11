# ChatAtender — Agente IA para WhatsApp

Bot de vendas com IA rodando sobre a [Evolution API](https://github.com/EvolutionAPI/evolution-api).  
Qualifica leads com metodologia BANT e conduz à compra automaticamente, 24/7.

---

## Arquitetura

```
webhook_server.py   ← Servidor HTTP principal (porta 5000)
├── config.py       ← Configuração centralizada (.env)
├── worker.py       ← Fila de mensagens + thread pool
├── evolution_client.py  ← Cliente Evolution API (envio, typing, mídia)
├── transcriber.py  ← Transcrição de áudio (OpenAI Whisper)
├── dashboard.py    ← Painel web de monitoramento
├── agent.py        ← Orquestra sessão + resposta
├── agent_core.py   ← Chamadas à IA (Anthropic/OpenAI/Gemini)
└── sessions.py     ← SQLite — leads, mensagens, sessões
```

**Fluxo de uma mensagem:**
1. Evolution API → `POST /webhook`
2. Deduplicação por message ID
3. Enfileiramento no worker pool
4. (Opcional) Download e transcrição de áudio via Whisper
5. Indicador "digitando..." enviado ao usuário
6. Agente IA processa com histórico de sessão (BANT)
7. Resposta enviada via Evolution API com retry automático

---

## Pré-requisitos

- Python 3.11+
- Evolution API rodando (local ou remota)
- Instância WhatsApp conectada no Evolution API
- Chave Anthropic, OpenAI ou Gemini

---

## Instalação

```powershell
# Clone ou baixe os arquivos para uma pasta
cd C:\Users\<seu-usuario>\meu-agente

# Nenhuma dependência externa — apenas stdlib Python
python --version   # precisa ser 3.11+
```

---

## Configuração

Edite o arquivo `.env`:

```ini
# Provedor de IA
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Evolution API
EVOLUTION_API_KEY=sua-chave-aqui
EVOLUTION_URL=http://localhost:8080
INSTANCE_NAME=meu-agente

# Webhook
WEBHOOK_PORT=5000
WEBHOOK_PUBLIC_URL=https://xxxx.ngrok-free.app   # opcional

# Transcrição de áudio (requer OPENAI_API_KEY)
AUDIO_TRANSCRIPTION=false
OPENAI_API_KEY=sk-...

# Dashboard
DASHBOARD_USER=admin
DASHBOARD_PASS=chatatender
```

---

## Execução

```powershell
python webhook_server.py
```

Saída esperada:
```
════════════════════════════════════════════════════════════
  ChatAtender Webhook Server
  Porta       : 5000
  Instância   : meu-agente
  Provedor IA : ANTHROPIC
  Workers     : 4 threads
  Transcrição : ✗ desativada
  Dashboard   : http://localhost:5000/dashboard
  Health      : http://localhost:5000/health
════════════════════════════════════════════════════════════
```

---

## Expondo publicamente (ngrok)

Para receber webhooks da Evolution API em produção:

```powershell
# Terminal 1 — servidor
python webhook_server.py

# Terminal 2 — túnel ngrok
ngrok http 5000
```

Copie a URL gerada (ex: `https://abc123.ngrok-free.app`) e coloque em `WEBHOOK_PUBLIC_URL` no `.env`.  
O servidor registra o webhook automaticamente na Evolution API ao iniciar.

---

## Endpoints

| Endpoint           | Método | Descrição                                  |
|--------------------|--------|--------------------------------------------|
| `/webhook`         | POST   | Recebe eventos da Evolution API            |
| `/health`          | GET    | Status do servidor + workers + instância   |
| `/metrics`         | GET    | Métricas JSON do worker pool               |
| `/dashboard`       | GET    | Painel web (Basic Auth)                    |
| `/dashboard/data`  | GET    | Stats em JSON (Basic Auth)                 |

---

## Dashboard

Acesse `http://localhost:5000/dashboard` no navegador.

- **Login:** `admin` / `chatatender` (configure em `.env`)
- Auto-refresh a cada 30 segundos
- Mostra: leads, checkouts, mensagens, fila, conversas recentes

---

## Transcrição de Áudio

Quando `AUDIO_TRANSCRIPTION=true` e `OPENAI_API_KEY` preenchida:
- Mensagens de voz são baixadas automaticamente
- Transcritas via OpenAI Whisper (`whisper-1`, forçado pt-BR)
- Texto transcrito segue o fluxo normal do agente

---

## Variáveis de Ambiente Completas

| Variável             | Padrão        | Descrição                              |
|----------------------|---------------|----------------------------------------|
| `AI_PROVIDER`        | `anthropic`   | `anthropic` / `openai` / `gemini`      |
| `ANTHROPIC_API_KEY`  | —             | Chave Anthropic                        |
| `OPENAI_API_KEY`     | —             | Chave OpenAI (também para Whisper)     |
| `GEMINI_API_KEY`     | —             | Chave Google Gemini                    |
| `EVOLUTION_API_KEY`  | —             | Chave da Evolution API                 |
| `EVOLUTION_URL`      | `localhost:8080` | URL da Evolution API               |
| `INSTANCE_NAME`      | `meu-agente`  | Nome da instância WhatsApp             |
| `WEBHOOK_PORT`       | `5000`        | Porta do servidor                      |
| `WEBHOOK_PUBLIC_URL` | —             | URL pública para registro do webhook   |
| `WEBHOOK_SECRET`     | —             | Bearer token para validar requisições  |
| `AUDIO_TRANSCRIPTION`| `false`       | Ativar transcrição de áudio            |
| `TYPING_ENABLED`     | `true`        | Enviar indicador "digitando..."        |
| `TYPING_DELAY`       | `1.5`         | Segundos do indicador de digitação     |
| `WORKER_THREADS`     | `4`           | Threads paralelas de processamento     |
| `MAX_RETRIES`        | `3`           | Tentativas para envio de mensagem      |
| `SESSION_TTL`        | `1800`        | Timeout de sessão em segundos          |
| `DASHBOARD_ENABLED`  | `true`        | Habilitar painel web                   |
| `DASHBOARD_USER`     | `admin`       | Usuário do dashboard                   |
| `DASHBOARD_PASS`     | `chatatender` | Senha do dashboard                     |
