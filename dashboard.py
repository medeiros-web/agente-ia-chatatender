"""
Dashboard web — painel de monitoramento do agente ChatAtender.
Serve HTML com auto-refresh e dados em tempo real do SQLite.
Autenticação HTTP Basic.
"""
import base64
import json
import logging
from datetime import datetime, timedelta

import config

log = logging.getLogger(__name__)

# ── Queries de stats ──────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Coleta métricas do banco de dados e do worker."""
    try:
        from sessions import _db
        import worker
        conn = _db()

        total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        checkout_sent = conn.execute("SELECT COUNT(*) FROM leads WHERE sent_checkout=1").fetchone()[0]

        today = datetime.now().strftime("%Y-%m-%d")
        msgs_today = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE datetime(ts,'unixepoch') >= ?",
            (today,)
        ).fetchone()[0]

        msgs_total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        # Leads dos últimos 7 dias
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        leads_week = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]

        # Últimas 10 conversas
        recent = conn.execute("""
            SELECT l.phone, l.name, l.created_at, l.sent_checkout,
                   COUNT(m.id) as msg_count,
                   MAX(m.ts) as last_ts
            FROM leads l
            LEFT JOIN messages m ON m.lead_id = l.id
            GROUP BY l.id
            ORDER BY last_ts DESC NULLS LAST
            LIMIT 10
        """).fetchall()

        conn.close()

        recent_leads = []
        for row in recent:
            last_ts = datetime.fromtimestamp(row["last_ts"]).strftime("%d/%m %H:%M") if row["last_ts"] else "—"
            recent_leads.append({
                "phone":        row["phone"],
                "name":         row["name"] or row["phone"],
                "created_at":   row["created_at"][:16].replace("T", " "),
                "sent_checkout": bool(row["sent_checkout"]),
                "msg_count":    row["msg_count"],
                "last_activity": last_ts,
            })

        worker_snap = worker.metrics.snapshot()

        return {
            "total_leads":    total_leads,
            "checkout_sent":  checkout_sent,
            "msgs_today":     msgs_today,
            "msgs_total":     msgs_total,
            "leads_week":     leads_week,
            "recent_leads":   recent_leads,
            "worker":         worker_snap,
            "instance":       config.INSTANCE_NAME,
            "ai_provider":    config.AI_PROVIDER,
            "updated_at":     datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        log.exception("Erro ao coletar stats: %s", e)
        return {"error": str(e)}


def check_auth(auth_header: str | None) -> bool:
    """Valida Basic Auth do dashboard."""
    if not config.DASHBOARD_PASS:
        return True
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        user, pwd = decoded.split(":", 1)
        return user == config.DASHBOARD_USER and pwd == config.DASHBOARD_PASS
    except Exception:
        return False


# ── HTML do dashboard ─────────────────────────────────────────────────────────

def render_html(stats: dict) -> str:
    worker = stats.get("worker", {})
    leads  = stats.get("recent_leads", [])

    rows = ""
    for l in leads:
        checkout = "✅" if l["sent_checkout"] else "—"
        rows += f"""
        <tr>
          <td>{l['name']}</td>
          <td class="mono">{l['phone']}</td>
          <td>{l['msg_count']}</td>
          <td>{checkout}</td>
          <td>{l['last_activity']}</td>
          <td>{l['created_at']}</td>
        </tr>"""

    error_banner = f'<div class="banner error">⚠️ {stats.get("error", "")}</div>' if "error" in stats else ""

    conversion = 0
    if stats.get("total_leads", 0):
        conversion = round(stats["checkout_sent"] / stats["total_leads"] * 100, 1)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>ChatAtender — Dashboard</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
    header{{background:linear-gradient(135deg,#1e40af,#7c3aed);padding:20px 32px;display:flex;align-items:center;justify-content:space-between}}
    header h1{{font-size:1.5rem;font-weight:700;letter-spacing:.5px}}
    header small{{font-size:.8rem;opacity:.8}}
    .badge{{background:rgba(255,255,255,.2);border-radius:20px;padding:3px 10px;font-size:.75rem;margin-left:10px}}
    .container{{padding:28px 32px;max-width:1400px;margin:0 auto}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:28px}}
    .card{{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155}}
    .card .label{{font-size:.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}}
    .card .value{{font-size:2rem;font-weight:700;color:#f1f5f9}}
    .card .sub{{font-size:.8rem;color:#64748b;margin-top:4px}}
    .card.green .value{{color:#4ade80}}
    .card.blue  .value{{color:#60a5fa}}
    .card.yellow .value{{color:#fbbf24}}
    .card.purple .value{{color:#c084fc}}
    .section{{background:#1e293b;border-radius:12px;border:1px solid #334155;overflow:hidden;margin-bottom:20px}}
    .section-header{{padding:16px 20px;background:#0f172a;border-bottom:1px solid #334155;font-weight:600;font-size:.9rem;color:#94a3b8}}
    table{{width:100%;border-collapse:collapse}}
    th{{padding:10px 16px;text-align:left;font-size:.75rem;color:#64748b;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #334155}}
    td{{padding:12px 16px;font-size:.875rem;border-bottom:1px solid #1e293b}}
    tr:last-child td{{border-bottom:none}}
    tr:hover td{{background:rgba(255,255,255,.03)}}
    .mono{{font-family:monospace;font-size:.8rem;color:#94a3b8}}
    .pill{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:.7rem;font-weight:600}}
    .pill.on{{background:#166534;color:#4ade80}}
    .pill.off{{background:#374151;color:#9ca3af}}
    .worker-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:16px}}
    .witem{{text-align:center}}
    .witem .wval{{font-size:1.5rem;font-weight:700;color:#60a5fa}}
    .witem .wlbl{{font-size:.7rem;color:#64748b;margin-top:2px}}
    .banner.error{{background:#7f1d1d;color:#fca5a5;padding:12px 20px;border-radius:8px;margin-bottom:16px}}
    footer{{text-align:center;padding:20px;color:#334155;font-size:.75rem}}
    .updated{{font-size:.7rem;color:#475569;margin-top:4px}}
  </style>
</head>
<body>
<header>
  <div>
    <h1>🤖 ChatAtender <span class="badge">{stats.get("instance","")}</span></h1>
    <small>Painel de Monitoramento <span class="badge">{stats.get("ai_provider","").upper()}</span></small>
  </div>
  <div style="text-align:right">
    <div style="font-size:.9rem;font-weight:600">Status <span class="pill on">● Online</span></div>
    <div class="updated">Atualizado às {stats.get("updated_at","")}</div>
  </div>
</header>

<div class="container">
  {error_banner}

  <!-- Cards de métricas -->
  <div class="grid">
    <div class="card blue">
      <div class="label">Total de Leads</div>
      <div class="value">{stats.get("total_leads", 0)}</div>
      <div class="sub">+{stats.get("leads_week", 0)} nos últimos 7 dias</div>
    </div>
    <div class="card green">
      <div class="label">Checkouts Enviados</div>
      <div class="value">{stats.get("checkout_sent", 0)}</div>
      <div class="sub">Conversão: {conversion}%</div>
    </div>
    <div class="card yellow">
      <div class="label">Mensagens Hoje</div>
      <div class="value">{stats.get("msgs_today", 0)}</div>
      <div class="sub">Total: {stats.get("msgs_total", 0)}</div>
    </div>
    <div class="card purple">
      <div class="label">Fila Atual</div>
      <div class="value">{worker.get("queue_size", 0)}</div>
      <div class="sub">Workers ativos: {worker.get("active_workers", 0)}</div>
    </div>
    <div class="card">
      <div class="label">Processadas</div>
      <div class="value">{worker.get("processed", 0)}</div>
      <div class="sub">Erros: {worker.get("errors", 0)}</div>
    </div>
    <div class="card">
      <div class="label">Transcrições</div>
      <div class="value">{worker.get("transcribed", 0)}</div>
      <div class="sub">Áudios processados</div>
    </div>
  </div>

  <!-- Workers -->
  <div class="section" style="margin-bottom:20px">
    <div class="section-header">⚙️ Workers</div>
    <div class="worker-grid">
      <div class="witem"><div class="wval">{worker.get("processed", 0)}</div><div class="wlbl">Processadas</div></div>
      <div class="witem"><div class="wval">{worker.get("errors", 0)}</div><div class="wlbl">Erros</div></div>
      <div class="witem"><div class="wval">{worker.get("active_workers", 0)}</div><div class="wlbl">Ativos agora</div></div>
      <div class="witem"><div class="wval">{worker.get("queue_size", 0)}</div><div class="wlbl">Na fila</div></div>
    </div>
  </div>

  <!-- Tabela de leads recentes -->
  <div class="section">
    <div class="section-header">👥 Conversas Recentes</div>
    <table>
      <thead>
        <tr>
          <th>Nome</th><th>Telefone</th><th>Msgs</th><th>Checkout</th><th>Última atividade</th><th>Cadastro</th>
        </tr>
      </thead>
      <tbody>
        {rows if rows else '<tr><td colspan="6" style="text-align:center;color:#64748b;padding:32px">Nenhuma conversa ainda</td></tr>'}
      </tbody>
    </table>
  </div>
</div>

<footer>ChatAtender Agent · Python {__import__('sys').version.split()[0]} · Auto-refresh 30s</footer>
</body>
</html>"""
