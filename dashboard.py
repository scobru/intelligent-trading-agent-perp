"""
Dashboard web dell'agente SynFutures: valore del conto, posizioni in leva,
dati decisionali dell'AI (sentiment, Prophet, notizie, indicatori),
storico delle operazioni ed errori. In paper trading mostra anche il
pannello del conto virtuale.

Stile e helper arrivano da static/dashboard.css e static/dashboard.js,
condivisi con le dashboard dei bot fratelli (degen, yield). Il pulsante
"Esegui ciclo" e' attivo solo se DASHBOARD_RUN_TOKEN e' impostato: la
dashboard non ha login e un ciclo puo' firmare transazioni.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("DASHBOARD_PORT", os.getenv("PORT", "3000")))
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db"))
import config
import dashboard_auth  # noqa: E402
import db_utils  # noqa: E402
RUN_TOKEN = os.getenv("DASHBOARD_RUN_TOKEN", "")
PAPER_TRADING = os.getenv("PAPER_TRADING", "false").strip().lower() in ("1", "true", "yes", "on")
# Soglie dell'ETH per il gas nel wallet: sotto MIN il bot non riesce a
# firmare, sotto WARN la dashboard e Telegram chiedono di ricaricare
GAS_MIN_ETH = float(os.getenv("GAS_MIN_ETH", "0.0005"))
GAS_WARN_ETH = float(os.getenv("GAS_WARN_ETH", "0.002"))

# Asset statici serviti dalla dashboard (allowlist esplicita: nessun path
# arbitrario arriva al filesystem)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_ROUTES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/static/icon.svg": ("icon.svg", "image/svg+xml"),
    "/static/icon-small.svg": ("icon-small.svg", "image/svg+xml"),
    "/static/icon-192.png": ("icon-192.png", "image/png"),
    "/static/icon-512.png": ("icon-512.png", "image/png"),
    "/static/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/static/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/static/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/static/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}

_run_lock = threading.Lock()

# Cache in-memory per dati live (60s)
_LIVE_CACHE = {
    "sentiment": None,
    "sentiment_time": 0,
    "news": None,
    "news_time": 0
}

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Intelligent Trading Agent</title>
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="manifest" href="/static/site.webmanifest">
<meta name="theme-color" content="#3b82f6">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/dashboard.css?v=3">
<style>:root { --primary: #3b82f6; --accent: #6366f1; }</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://s3.tradingview.com/tv.js"></script>
<script src="/static/dashboard.js?v=3"></script>
</head>
<body>
<header class="header">
  <div class="brand">
    <img src="/static/icon.svg" alt="">
    <div>
      <h1>Intelligent Trading Agent <span class="badge b-no" id="mode">…</span></h1>
      <p class="tagline">Perpetual SynFutures V3 su Base • OpenRouter • SQLite</p>
    </div>
  </div>
  <div class="header-actions">
    <span class="updated" id="updated"></span>
    <button class="btn" id="deposit-btn" onclick="depositGate()" style="background: rgba(34, 197, 94, 0.15); border: 1px solid rgba(34, 197, 94, 0.4); color: #22c55e;">📥 Deposita su Gate</button>
    <button class="btn" id="run">⚡ Esegui ciclo ora</button>
  </div>
</header>

<section class="card paper-panel" id="paper-panel" hidden>
  <div class="card-head"><h2>📝 Paper trading <small>conto virtuale, prezzi reali</small></h2></div>
  <div class="paper-grid" id="paper-grid"></div>
  <p class="note" id="paper-note"></p>
</section>

<section class="card wallet-bar" id="wallet-panel" hidden>
  <div class="wallet-items" id="wallet-items"></div>
  <p class="note" id="wallet-note" hidden></p>
</section>

<section class="stats">
  <div class="card"><h3>Valore totale</h3><div class="value" id="total">--</div><div class="sub" id="total-sub"></div></div>
  <div class="card"><h3>Posizioni aperte</h3><div class="value" id="npos">--</div><div class="sub" id="npos-sub"></div></div>
  <div class="card"><h3>Fear &amp; Greed</h3><div class="value" id="fng-kpi">--</div><div class="sub" id="fng-kpi-sub">Alternative.me</div></div>
  <div class="card"><h3>Ultima decisione</h3><div class="value" id="last-op">--</div><div class="sub" id="last-op-time">nessuna operazione registrata</div></div>
</section>

<section class="card section">
  <div class="card-head">
    <div class="tabs" data-tabs="chart">
      <button class="tab active" data-tab="equity">💼 Andamento capitale</button>
      <button class="tab" data-tab="market">📈 Mercato live</button>
    </div>
    <div class="pills" id="symbol-pills" hidden>
      <button class="pill active" data-symbol="BINANCE:ETHUSDC">ETH</button>
      <button class="pill" data-symbol="BINANCE:BTCUSDC">BTC</button>
    </div>
  </div>
  <div class="chart-box tall" id="equity-view"><canvas id="equity"></canvas></div>
  <div class="tv-box" id="market-view" hidden><div id="tradingview_widget" style="height:100%"></div></div>
</section>

<div class="grid-2">
  <section class="card">
    <div class="card-head"><h2>📊 Posizioni aperte</h2><small id="pos-note">SynFutures V3</small></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Simbolo</th><th>Lato</th><th>Quantità</th><th>Ingresso</th><th>Mark</th><th>Leva</th><th>Margine</th><th>P&amp;L</th></tr></thead>
      <tbody id="positions"></tbody>
    </table></div>
  </section>
  <section class="card">
    <div class="card-head"><h2>🧠 Ultima decisione AI</h2></div>
    <div class="decision-box">
      <div class="title" id="ai-action">In attesa del primo ciclo...</div>
      <div class="desc" id="ai-reason">L'agente elaborerà i dati al prossimo intervallo o con "Esegui ciclo ora".</div>
    </div>
    <div class="kv">
      <div><b>Modello:</b> OpenRouter</div>
      <div><b>Input:</b> indicatori + notizie + sentiment + Prophet</div>
    </div>
  </section>
</div>

<h2 class="section-title">🧠 Dati decisionali dell'AI <small>fattori analizzati dall'agente a ogni ciclo</small></h2>
<div class="grid-3">
  <section class="card">
    <div class="card-head"><h3>🎭 Fear &amp; Greed</h3><span class="badge b-no" id="fng-badge">--</span></div>
    <div><span class="value" id="fng-score">--</span> <small>/ 100</small></div>
    <div class="meter"><div id="fng-bar" style="width:50%"></div></div>
    <div class="pills muted" style="justify-content:space-between;font-size:10px"><span>Extreme Fear</span><span>Neutral</span><span>Extreme Greed</span></div>
    <p class="note">Fonte: Alternative.me. Il sentiment modula l'esposizione al rischio.</p>
  </section>
  <section class="card">
    <div class="card-head"><h3>🔮 Previsioni Prophet</h3><span class="badge b-info">15m • 1h</span></div>
    <div id="forecasts" class="scroll"></div>
  </section>
  <section class="card">
    <div class="card-head"><h3>📰 Notizie</h3><span class="badge b-info">CoinJournal RSS</span></div>
    <div id="news" class="scroll"></div>
  </section>
</div>

<section class="card section">
  <div class="card-head"><h2>📐 Indicatori tecnici <small>BTC ed ETH su 15m e 1h</small></h2></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Ticker</th><th>Prezzo</th><th>RSI (7)</th><th>MACD</th><th>EMA 20</th><th>Pivot</th><th>S1</th><th>R1</th><th>Bias</th></tr></thead>
    <tbody id="indicators"></tbody>
  </table></div>
</section>

<section class="card section">
  <div class="card-head"><h2>📜 Storico operazioni</h2></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Data (UTC)</th><th>Operazione</th><th>Simbolo</th><th>Direzione</th><th>Allocazione</th><th>Leva</th><th>Motivazione</th></tr></thead>
    <tbody id="ops"></tbody>
  </table></div>
</section>

<section class="card section">
  <div class="card-head"><h2>⚠️ Errori recenti</h2></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Data (UTC)</th><th>Tipo</th><th>Messaggio</th></tr></thead>
    <tbody id="errors"></tbody>
  </table></div>
</section>

<footer class="footer">Intelligent Trading Agent • SynFutures V3 (Base) &amp; OpenRouter • CapRover &amp; Docker</footer>

<script>
const { $, esc, usd, signedUsd, signedPct, pct, price, cls, time, empty, sideBadge } = ITA;
let chart = null, data = null, chartTab = 'equity', tvSymbol = 'BINANCE:ETHUSDC';

function initTradingView() {
  if (typeof TradingView === 'undefined') return;
  $('tradingview_widget').innerHTML = '';
  new TradingView.widget({ autosize: true, symbol: tvSymbol, interval: '15', timezone: 'Etc/UTC',
    theme: 'dark', style: '1', locale: 'it', toolbar_bg: '#131b2e', enable_publishing: false,
    save_image: false, container_id: 'tradingview_widget' });
}

function renderChart() {
  const h = data?.balance_history || [];
  chart = ITA.lineChart(chart, $('equity'), h.map(p => time(p.time)), [
    { label: 'Valore totale', data: h.map(p => p.total) },
    { label: 'Collaterale libero', data: h.map(p => p.balance) },
  ]);
}

function renderKpi(d) {
  $('total').textContent = usd(d.total_value);
  const pnl = d.meta?.paper?.pnl_usd;
  $('total-sub').innerHTML = d.meta?.mode === 'paper' && pnl != null
    ? `<span class="${cls(pnl)}">${signedUsd(pnl)}</span> dall'inizio (paper)`
    : 'collaterale libero ' + usd(d.balance);
  const pos = d.positions || [];
  $('npos').textContent = pos.length;
  const upnl = pos.reduce((a, p) => a + (Number(p.pnl_usd) || 0), 0);
  $('npos-sub').innerHTML = pos.length ? `P&L aperto <span class="${cls(upnl)}">${signedUsd(upnl)}</span>` : 'nessuna esposizione';
  const s = d.sentiment || {};
  $('fng-kpi').textContent = s.value != null ? s.value + ' / 100' : '--';
  $('fng-kpi-sub').textContent = s.classification || 'Alternative.me';
  const last = (d.operations || [])[0];
  $('last-op').textContent = last ? ((last.operation || '--').toUpperCase() + ' ' + (last.symbol || '')) : '--';
  $('last-op-time').textContent = last ? time(last.created_at) + ' UTC' : 'nessuna operazione registrata';
}

function renderPositions(pos) {
  $('pos-note').textContent = data.meta?.mode === 'paper' ? 'conto virtuale' : 'SynFutures V3';
  $('positions').innerHTML = pos.map(p => `<tr>
    <td><b>${esc(p.symbol)}</b></td><td>${sideBadge(p.side)}</td><td class="num">${esc(p.size)}</td>
    <td class="num">${price(p.entry_price)}</td><td class="num">${price(p.mark_price)}</td>
    <td class="num">${esc(p.leverage || '--')}</td><td class="num">${usd(p.margin_usd)}</td>
    <td class="num ${cls(p.pnl_usd)}"><b>${signedUsd(p.pnl_usd)}</b></td></tr>`).join('')
    || empty(8, 'Nessuna posizione aperta.');
}

function renderIntel(d) {
  const s = d.sentiment || {};
  if (s.value != null) {
    const v = Number(s.value);
    $('fng-score').textContent = v;
    $('fng-bar').style.width = Math.min(Math.max(v, 5), 100) + '%';
    $('fng-badge').textContent = String(s.classification || '').toUpperCase();
    $('fng-badge').className = 'badge ' + (v >= 60 ? 'b-ok' : v <= 40 ? 'b-bad' : 'b-no');
  }
  $('forecasts').innerHTML = (d.forecasts || []).map(f => {
    const ok = f.prediction !== null && f.prediction !== undefined;
    return `<div class="list-item row"><div><b>${esc(f.ticker)}</b> <small>(${esc(f.timeframe)})</small>
      <div class="muted">${price(f.last_price)} ➔ <b>${ok ? price(f.prediction) : 'N/D'}</b></div></div>
      <div style="text-align:right"><b class="${ok ? cls(f.change_pct) : 'muted'}">${ok ? signedPct(f.change_pct) : 'N/D'}</b>
      <div class="muted" style="font-size:10px">${ok ? '[' + Number(f.lower_bound || 0).toFixed(0) + ' - ' + Number(f.upper_bound || 0).toFixed(0) + ']' : 'non disponibile'}</div></div></div>`;
  }).join('') || '<p class="muted">In attesa delle previsioni del prossimo ciclo...</p>';
  $('news').innerHTML = (d.news || []).map(n => `<div class="list-item news">${esc(n)}</div>`).join('')
    || '<p class="muted">Nessuna notizia recente.</p>';
  $('indicators').innerHTML = (d.indicators || []).map(i => {
    const rsi = i.rsi_7 ?? 50, p = i.price || 0, ema = i.ema20 || 0;
    const rsiB = rsi > 70 ? `<span class="badge b-bad">${rsi.toFixed(1)} ipercomprato</span>`
      : rsi < 30 ? `<span class="badge b-ok">${rsi.toFixed(1)} ipervenduto</span>` : `<span class="badge b-no">${rsi.toFixed(1)}</span>`;
    const bias = p > ema && rsi > 50 ? '<span class="badge b-ok">Bullish</span>'
      : p < ema && rsi < 50 ? '<span class="badge b-bad">Bearish</span>' : '<span class="badge b-no">Range</span>';
    return `<tr><td><b>${esc(i.ticker)}</b></td><td class="num">${price(p)}</td><td>${rsiB}</td>
      <td class="num ${cls(i.macd)}">${Number(i.macd || 0).toFixed(2)}</td><td class="num">${price(ema)}</td>
      <td class="num muted">${price(i.pp)}</td><td class="num pos">${price(i.s1)}</td><td class="num neg">${price(i.r1)}</td><td>${bias}</td></tr>`;
  }).join('') || empty(9, 'In attesa del calcolo indicatori del primo ciclo...');
}

function renderOps(ops) {
  const last = ops[0];
  if (last) {
    $('ai-action').textContent = `${(last.operation || '').toUpperCase()} ${last.symbol || ''} ${last.direction ? '(' + last.direction.toUpperCase() + ')' : ''}`;
    $('ai-reason').textContent = last.reason || 'Nessuna spiegazione salvata.';
  }
  $('ops').innerHTML = ops.map(o => `<tr><td class="num">${esc(time(o.created_at))}</td>
    <td>${sideBadge(o.operation)}</td><td><b>${esc(o.symbol || '--')}</b></td>
    <td>${esc((o.direction || '--').toUpperCase())}</td>
    <td class="num">${o.target_portion_of_balance ? (o.target_portion_of_balance * 100).toFixed(0) + '%' : '--'}</td>
    <td class="num">${o.leverage ? esc(o.leverage) + 'x' : '--'}</td>
    <td class="reason">${esc(o.reason || '')}</td></tr>`).join('') || empty(7, 'Nessuna operazione registrata.');
}

async function load() {
  try { data = await (await fetch('/api/status')).json(); }
  catch (e) { $('updated').textContent = 'dashboard non raggiungibile'; return; }
  ITA.renderMeta(data.meta);
  renderKpi(data);
  renderPositions(data.positions || []);
  renderIntel(data);
  renderOps(data.operations || []);
  ITA.renderErrors(data.errors);
  if (chartTab === 'equity') renderChart();
}

ITA.setupTabs('chart', (tab) => {
  chartTab = tab;
  $('equity-view').hidden = tab !== 'equity';
  $('market-view').hidden = tab !== 'market';
  $('symbol-pills').hidden = tab !== 'market';
  if (tab === 'market') initTradingView(); else renderChart();
});
document.querySelectorAll('#symbol-pills .pill').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('#symbol-pills .pill').forEach(x => x.classList.toggle('active', x === b));
  tvSymbol = b.dataset.symbol; initTradingView();
}));
async function depositGate() {
  if (!confirm('Vuoi depositare tutti gli USDC disponibili nel wallet sul contratto Gate di SynFutures?')) return;
  let token = '';
  try { token = localStorage.getItem('runToken') || ''; } catch (e) {}
  if (!token) {
    token = prompt('Token di autorizzazione (DASHBOARD_RUN_TOKEN):') || '';
    if (!token) return;
  }
  const btn = $('deposit-btn');
  const prevText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Deposito in corso...'; }
  try {
    const r = await fetch('/api/deposit_gate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Run-Token': token
      },
      body: JSON.stringify({ amount: 0 })
    });
    const res = await r.json().catch(() => ({}));
    if (r.ok && res.status === 'success') {
      try { localStorage.setItem('runToken', token); } catch (e) {}
      alert('Deposito sul Gate completato con successo!');
      setTimeout(load, 2000);
    } else if (r.status === 403) {
      try { localStorage.removeItem('runToken'); } catch (e) {}
      alert('Errore autorizzazione: ' + (res.message || 'Token non valido o DASHBOARD_RUN_TOKEN non configurato.'));
    } else {
      alert('Errore deposito Gate: ' + (res.message || res.error || r.statusText || 'Errore sconosciuto'));
    }
  } catch(e) {
    alert('Errore chiamata: ' + e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prevText; }
  }
}

ITA.setupRun(load);
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""


def _signed_usd(v) -> str:
    v = float(v or 0.0)
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _paper_meta(payload):
    """Normalizza il riepilogo del conto paper nello schema comune delle dashboard."""
    p = payload.get("paper") or {}
    return {
        "initial_usd": p.get("initial_usdc"),
        "value_usd": p.get("equity_usd", payload.get("total_value_usd")),
        "pnl_usd": p.get("pnl_usd", payload.get("pnl_since_start_usd")),
        "operations": p.get("trades"),
        "costs_usd": p.get("fees_paid_usd"),
        "costs_label": "Fee simulate",
        "started_at": p.get("created_at"),
        "extra": [
            ["P&L realizzato", _signed_usd(p.get("realized_pnl_usd", 0.0))],
            ["Stop loss scattati", str(p.get("stop_losses", 0))],
            ["Liquidazioni", str(p.get("liquidations", 0))],
        ],
        "note": "Riempimenti al prezzo di mercato (Binance/Kraken) con slippage e fee simulate; "
                "stop loss e liquidazioni controllati a ogni ciclo. Funding non simulato.",
    }


def get_db_data():
    global _LIVE_CACHE
    now = time.time()

    data = {
        "meta": {"mode": "paper" if PAPER_TRADING else None, "updated_at": None,
                 "run_enabled": bool(RUN_TOKEN), "paper": None},
        "balance": 0.0,
        "total_value": 0.0,
        "positions": [],
        "sentiment": {"value": 50, "classification": "Neutral"},
        "indicators": [],
        "forecasts": [],
        "news": [],
        "operations": [],
        "balance_history": [],
        "errors": []
    }

    if not os.path.exists(SQLITE_DB_PATH):
        return data

    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. Ultimo snapshot account
        cur.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1;")
        snap = cur.fetchone()
        if snap:
            try:
                payload = json.loads(snap["raw_payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            data["balance"] = float(snap["balance_usd"])
            data["total_value"] = float(payload.get("total_value_usd", snap["balance_usd"]))
            # gli snapshot precedenti al paper trading non hanno "mode": erano live
            data["meta"]["mode"] = payload.get("mode") or ("paper" if payload.get("paper_trading") else "live")
            data["meta"]["updated_at"] = snap["created_at"]
            if data["meta"]["mode"] == "paper":
                data["meta"]["paper"] = _paper_meta(payload)
            if payload.get("wallet_eth_balance") is not None:
                data["meta"]["wallet"] = {
                    "address": payload.get("wallet_address"),
                    "eth": payload.get("wallet_eth_balance"),
                    "min_eth": GAS_MIN_ETH,
                    "warn_eth": GAS_WARN_ETH,
                    "extra": [["USDC nel wallet", f"${float(payload.get('wallet_usdc_balance') or 0):,.2f}"],
                              ["Collaterale Gate", f"${float(payload.get('balance_usd') or 0):,.2f}"]],
                }
            snapshot_id = snap["id"]

            # Posizioni associate
            cur.execute("SELECT * FROM open_positions WHERE snapshot_id = ?;", (snapshot_id,))
            positions = []
            for row in cur.fetchall():
                try:
                    extra = json.loads(row["raw_payload"] or "{}")
                except (TypeError, ValueError):
                    extra = {}
                positions.append({
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "size": row["size"],
                    "entry_price": row["entry_price"],
                    "mark_price": row["mark_price"],
                    "pnl_usd": row["pnl_usd"],
                    "leverage": row["leverage"],
                    "margin_usd": extra.get("margin_usd"),
                })
            data["positions"] = positions

        # 2. Storico valore (ultimi 200 snapshot)
        cur.execute("SELECT created_at, balance_usd, raw_payload FROM account_snapshots ORDER BY id DESC LIMIT 200;")
        history = []
        for r in reversed(cur.fetchall()):
            try:
                total = json.loads(r["raw_payload"] or "{}").get("total_value_usd")
            except (TypeError, ValueError):
                total = None
            balance = float(r["balance_usd"])
            history.append({"time": r["created_at"], "balance": balance,
                            "total": float(total) if total is not None else balance})
        data["balance_history"] = history

        # 3. Sentiment (da SQLite o fallback live cache)
        cur.execute("SELECT value, classification, sentiment_timestamp FROM sentiment_contexts ORDER BY id DESC LIMIT 1;")
        sent = cur.fetchone()
        if sent and sent["value"] is not None:
            data["sentiment"] = {
                "value": sent["value"],
                "classification": sent["classification"],
                "timestamp": sent["sentiment_timestamp"]
            }
        else:
            # Fallback a live fetch se non presente nel DB
            if _LIVE_CACHE["sentiment"] and (now - _LIVE_CACHE["sentiment_time"] < 60):
                data["sentiment"] = _LIVE_CACHE["sentiment"]
            else:
                try:
                    from sentiment import get_latest_fear_and_greed
                    live_sent = get_latest_fear_and_greed()
                    if live_sent:
                        sent_obj = {
                            "value": live_sent.get("valore", 50),
                            "classification": live_sent.get("classificazione", "Neutral"),
                            "timestamp": live_sent.get("timestamp")
                        }
                        _LIVE_CACHE["sentiment"] = sent_obj
                        _LIVE_CACHE["sentiment_time"] = now
                        data["sentiment"] = sent_obj
                except Exception:
                    pass

        # 4. Indicatori tecnici (ultimi per ciascun ticker)
        cur.execute("""
            SELECT ticker, ts, price, ema20, macd, rsi_7, pp, s1, s2, r1, r2, funding_rate
            FROM indicators_contexts
            WHERE id IN (SELECT MAX(id) FROM indicators_contexts GROUP BY ticker)
            ORDER BY ticker ASC;
        """)
        data["indicators"] = [dict(r) for r in cur.fetchall()]
        wallet = data["meta"].get("wallet")
        eth_px = next((i["price"] for i in data["indicators"] if i["ticker"] == "ETH" and i["price"]), None)
        if wallet and eth_px:
            wallet["eth_usd"] = float(wallet["eth"]) * float(eth_px)

        # 5. Previsioni Prophet AI (ultime per ciascun ticker e timeframe)
        cur.execute("""
            SELECT ticker, timeframe, last_price, prediction, lower_bound, upper_bound, change_pct, forecast_timestamp
            FROM forecasts_contexts
            WHERE id IN (SELECT MAX(id) FROM forecasts_contexts GROUP BY ticker, timeframe)
            ORDER BY ticker ASC, timeframe ASC;
        """)
        data["forecasts"] = [dict(r) for r in cur.fetchall()]

        # 6. Notizie di mercato (da SQLite o fallback cache da CoinJournal)
        cur.execute("SELECT news_text FROM news_contexts ORDER BY id DESC LIMIT 1;")
        news_row = cur.fetchone()
        if news_row and news_row["news_text"]:
            data["news"] = [n.strip() for n in news_row["news_text"].split("\n\n") if n.strip()][:5]
        else:
            if _LIVE_CACHE["news"] and (now - _LIVE_CACHE["news_time"] < 120):
                data["news"] = _LIVE_CACHE["news"]
            else:
                try:
                    from news_feed import fetch_latest_news
                    live_news_txt = fetch_latest_news(max_chars=2500)
                    if live_news_txt:
                        news_items = [n.strip() for n in live_news_txt.split("\n\n") if n.strip()][:5]
                        _LIVE_CACHE["news"] = news_items
                        _LIVE_CACHE["news_time"] = now
                        data["news"] = news_items
                except Exception:
                    pass

        # 7. Ultime 20 operazioni
        cur.execute("SELECT * FROM bot_operations ORDER BY id DESC LIMIT 20;")
        ops = []
        for row in cur.fetchall():
            try:
                reason = json.loads(row["raw_payload"] or "{}").get("reason")
            except (TypeError, ValueError, AttributeError):
                reason = None
            ops.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "operation": row["operation"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "target_portion_of_balance": row["target_portion_of_balance"],
                "leverage": row["leverage"],
                "reason": reason,
                "raw_payload": row["raw_payload"]
            })
        data["operations"] = ops

        # 8. Errori recenti
        cur.execute("SELECT created_at, error_type, error_message, source FROM errors ORDER BY id DESC LIMIT 15;")
        data["errors"] = [dict(r) for r in cur.fetchall()]

        # 9. Controllo stato pausa
        data["is_paused"] = db_utils.is_bot_paused()
        data["pause_info"] = db_utils.get_pause_info()

        conn.close()
    except Exception as e:
        data["error"] = str(e)

    return data


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenzia i log standard di accesso per pulizia console

    def _json(self, code: int, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_auth_valid(self) -> bool:
        return dashboard_auth.is_run_token_valid(self.headers, RUN_TOKEN)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = HTML_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            self._json(200, get_db_data())
        elif parsed.path in STATIC_ROUTES:
            filename, content_type = STATIC_ROUTES[parsed.path]
            try:
                with open(os.path.join(STATIC_DIR, filename), "rb") as fh:
                    payload = fh.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/health":
            self._json(200, {"status": "healthy", "service": "dashboard"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/run", "/api/pause", "/api/resume", "/api/release_funds", "/api/withdraw_gate", "/api/deposit_gate"):
            self.send_response(404)
            self.end_headers()
            return

        if not self._is_auth_valid():
            self._json(403, {"message": "Token non valido o DASHBOARD_RUN_TOKEN non configurato."})
            return

        if path == "/api/pause":
            reason = "Pausa richiesta da API"
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    reason = body.get("reason", reason)
            except Exception:
                pass
            db_utils.set_bot_paused(True, reason=reason)
            self._json(200, {"status": "success", "is_paused": True, "message": f"Bot in pausa: {reason}"})
            return

        if path == "/api/resume":
            db_utils.set_bot_paused(False)
            self._json(200, {"status": "success", "is_paused": False, "message": "Bot riattivato con successo."})
            return

        if path in ("/api/release_funds", "/api/withdraw_gate"):
            target_amount = 0.0
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    target_amount = float(body.get("amount_usd", 0.0) or body.get("amount", 0.0))
            except Exception:
                pass
            try:
                from synfutures_trader import SynFuturesTrader
                trader = SynFuturesTrader()
                res = trader.release_funds(target_usdc=target_amount)
                self._json(200, res)
            except Exception as exc:
                self._json(500, {"status": "error", "message": str(exc)})
            return

        if path == "/api/deposit_gate":
            amount = 0.0
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    amount = float(body.get("amount", 0.0) or body.get("amount_usd", 0.0))
            except Exception:
                pass
            try:
                from synfutures_trader import SynFuturesTrader
                trader = SynFuturesTrader()
                if amount <= 0 and trader.client:
                    amount = float(trader.client.balance_of_float(config.USDC) or 0.0)
                if amount <= 0:
                    self._json(400, {"status": "error", "message": "Nessun USDC disponibile nel wallet da depositare (saldo: 0.00 USDC)."})
                    return
                res = trader.deposit_usdc(amount)
                self._json(200, {"status": "success", "result": res, "amount": amount})
            except Exception as exc:
                self._json(500, {"status": "error", "message": str(exc)})
            return

        if path == "/api/run":
            if db_utils.is_bot_paused():
                pinfo = db_utils.get_pause_info()
                self._json(200, {
                    "status": "paused",
                    "is_paused": True,
                    "message": f"Bot attualmente in PAUSA ({pinfo.get('reason', 'Pausa attiva')}). Ciclo ignorato."
                })
                return

            if not _run_lock.acquire(blocking=False):
                self._json(409, {"message": "Un ciclo e' gia' in corso."})
                return

            def _run():
                try:
                    subprocess.run([sys.executable, "main.py"], check=False)
                finally:
                    _run_lock.release()
            threading.Thread(target=_run, daemon=True).start()
            self._json(200, {"message": "Ciclo avviato: la dashboard si aggiorna da sola."})


def run_dashboard(port=PORT):
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"🌐 Dashboard Web attiva su http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_dashboard()
