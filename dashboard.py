import os
import json
import sqlite3
import threading
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("DASHBOARD_PORT", os.getenv("PORT", "3000")))
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db"))

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Intelligent Trading Agent - Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0f19;
            --surface: #131b2e;
            --surface-border: #1e2942;
            --primary: #3b82f6;
            --accent: #6366f1;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background: var(--bg);
            color: var(--text-main);
            padding: 24px;
            min-height: 100vh;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--surface-border);
        }
        .header h1 { font-size: 24px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .badge {
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 9999px;
            font-weight: 600;
        }
        .badge-live { background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }
        .badge-long { background: rgba(16, 185, 129, 0.2); color: var(--success); }
        .badge-short { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
        .btn {
            background: linear-gradient(135deg, var(--primary), var(--accent));
            color: #fff;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        .btn:hover { opacity: 0.9; }
        .grid-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--surface);
            border: 1px solid var(--surface-border);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
        }
        .card h3 { font-size: 13px; font-weight: 500; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
        .card .value { font-size: 26px; font-weight: 700; }
        .card .subtext { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .main-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }
        @media (max-width: 900px) { .main-grid { grid-template-columns: 1fr; } }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { text-align: left; padding: 12px; border-bottom: 1px solid var(--surface-border); font-size: 13px; }
        th { color: var(--text-muted); font-weight: 500; font-size: 12px; }
        .decision-box {
            background: #182238;
            border: 1px solid #2a395c;
            border-radius: 8px;
            padding: 14px;
            margin-top: 12px;
        }
        .decision-title { font-weight: 600; font-size: 15px; margin-bottom: 6px; color: #93c5fd; }
        .decision-desc { font-size: 13px; color: #d1d5db; line-height: 1.5; }
        .footer { text-align: center; color: var(--text-muted); font-size: 12px; margin-top: 32px; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🤖 Intelligent Trading Agent <span class="badge badge-live">● ONLINE</span></h1>
            <p style="color: var(--text-muted); font-size: 13px; margin-top: 4px;">SynFutures V3 (Base) • OpenRouter (openrouter/free) • SQLite</p>
        </div>
        <button class="btn" onclick="triggerRun()">⚡ Esegui Ciclo Ora</button>
    </div>

    <div class="grid-stats">
        <div class="card">
            <h3>Saldo Totale</h3>
            <div class="value" id="balance">$0.00</div>
            <div class="subtext">USDC su SynFutures Gate (Base)</div>
        </div>
        <div class="card">
            <h3>Posizioni Aperte</h3>
            <div class="value" id="open-positions-count">0</div>
            <div class="subtext">Asset attivi a mercato</div>
        </div>
        <div class="card">
            <h3>Sentiment Fear & Greed</h3>
            <div class="value" id="sentiment-val">-- / 100</div>
            <div class="subtext" id="sentiment-class">Alternative.me Feed</div>
        </div>
        <div class="card">
            <h3>Ultima Operazione</h3>
            <div class="value" id="last-op">--</div>
            <div class="subtext" id="last-op-time">Nessuna operazione registrata</div>
        </div>
    </div>

    <div class="main-grid">
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h2>📊 Posizioni Aperte Correnti</h2>
                <span id="pos-updated" style="font-size: 12px; color: var(--text-muted);">In attesa dati...</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Simbolo</th>
                        <th>Lato</th>
                        <th>Quantità</th>
                        <th>Prezzo Ingresso</th>
                        <th>Mark Price</th>
                        <th>Leva</th>
                        <th>PnL (USD)</th>
                    </tr>
                </thead>
                <tbody id="positions-table">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Nessuna posizione aperta.</td></tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2>🧠 Ultima Decisione AI</h2>
            <div class="decision-box">
                <div class="decision-title" id="ai-action">In attesa del primo ciclo...</div>
                <div class="decision-desc" id="ai-reason">L'agente elaborerà i dati di mercato al prossimo intervallo programmato o premendo 'Esegui Ciclo Ora'.</div>
            </div>
            <div style="margin-top: 14px; font-size: 12px; color: var(--text-muted);">
                <p><strong>Modello:</strong> OpenRouter (openrouter/free)</p>
                <p><strong>Timeframe:</strong> 15m Intraday + 1h Forecast</p>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>📜 Storico Ultime Operazioni (SQLite)</h2>
        <table>
            <thead>
                <tr>
                    <th>Data (UTC)</th>
                    <th>Operazione</th>
                    <th>Simbolo</th>
                    <th>Direzione</th>
                    <th>Allocazione</th>
                    <th>Leva</th>
                    <th>Dettagli / Payload</th>
                </tr>
            </thead>
            <tbody id="operations-table">
                <tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Caricamento storico...</td></tr>
            </tbody>
        </table>
    </div>

    <div class="footer">
        Intelligent Trading Agent • SynFutures Base DEX & OpenRouter • CapRover & Docker Edition
    </div>

    <script>
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                document.getElementById('balance').textContent = '$' + (data.balance || 0).toFixed(2);
                document.getElementById('open-positions-count').textContent = data.positions ? data.positions.length : 0;
                
                if (data.sentiment) {
                    document.getElementById('sentiment-val').textContent = (data.sentiment.value || '--') + ' / 100';
                    document.getElementById('sentiment-class').textContent = data.sentiment.classification || 'Neutral';
                }

                // Posizioni
                const posTable = document.getElementById('positions-table');
                if (data.positions && data.positions.length > 0) {
                    posTable.innerHTML = data.positions.map(p => `
                        <tr>
                            <td><strong>${p.symbol}</strong></td>
                            <td><span class="badge ${p.side === 'long' ? 'badge-long' : 'badge-short'}">${p.side.toUpperCase()}</span></td>
                            <td>${p.size}</td>
                            <td>$${(p.entry_price || 0).toFixed(2)}</td>
                            <td>$${(p.mark_price || 0).toFixed(2)}</td>
                            <td>${p.leverage || 'N/A'}</td>
                            <td style="color: ${p.pnl_usd >= 0 ? 'var(--success)' : 'var(--danger)'}; font-weight: 600;">
                                ${p.pnl_usd >= 0 ? '+' : ''}$${(p.pnl_usd || 0).toFixed(2)}
                            </td>
                        </tr>
                    `).join('');
                } else {
                    posTable.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Nessuna posizione aperta.</td></tr>';
                }

                // Ultima Operazione
                if (data.operations && data.operations.length > 0) {
                    const last = data.operations[0];
                    document.getElementById('last-op').textContent = (last.operation || '--').toUpperCase() + ' ' + (last.symbol || '');
                    document.getElementById('last-op-time').textContent = last.created_at;

                    document.getElementById('ai-action').textContent = `${(last.operation || '').toUpperCase()} ${last.symbol || ''} (${(last.direction || '').toUpperCase()})`;
                    
                    try {
                        const payload = JSON.parse(last.raw_payload);
                        document.getElementById('ai-reason').textContent = payload.reason || 'Nessuna spiegazione salvata.';
                    } catch(e) {
                        document.getElementById('ai-reason').textContent = 'Operazione registrata: ' + last.operation;
                    }

                    // Tabella Operazioni
                    const opsTable = document.getElementById('operations-table');
                    opsTable.innerHTML = data.operations.map(op => `
                        <tr>
                            <td>${op.created_at}</td>
                            <td><span class="badge" style="background:#1e2942;">${(op.operation || '').toUpperCase()}</span></td>
                            <td><strong>${op.symbol || '--'}</strong></td>
                            <td>${op.direction ? op.direction.toUpperCase() : '--'}</td>
                            <td>${(op.target_portion_of_balance ? (op.target_portion_of_balance * 100).toFixed(0) + '%' : '--')}</td>
                            <td>${op.leverage ? op.leverage + 'x' : '--'}</td>
                            <td style="font-family: monospace; font-size: 11px; color: var(--text-muted);">${op.raw_payload ? op.raw_payload.substring(0, 80) + '...' : '--'}</td>
                        </tr>
                    `).join('');
                } else {
                    document.getElementById('operations-table').innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Nessuna operazione registrata.</td></tr>';
                }

            } catch (err) {
                console.error('Errore aggiornamento dashboard:', err);
            }
        }

        async function triggerRun() {
            if (!confirm('Vuoi forzare un ciclo di trading adesso?')) return;
            try {
                const res = await fetch('/api/run', { method: 'POST' });
                const json = await res.json();
                alert(json.message || 'Ciclo avviato!');
                setTimeout(fetchStatus, 3000);
            } catch(e) {
                alert('Errore: ' + e);
            }
        }

        fetchStatus();
        setInterval(fetchStatus, 10000);
    </script>
</body>
</html>
"""

def get_db_data():
    data = {
        "balance": 0.0,
        "positions": [],
        "sentiment": {"value": 50, "classification": "Neutral"},
        "operations": [],
        "errors": []
    }
    if not os.path.exists(SQLITE_DB_PATH):
        return data

    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Ultimo snapshot account
        cur.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1;")
        snap = cur.fetchone()
        if snap:
            data["balance"] = float(snap["balance_usd"])
            snapshot_id = snap["id"]

            # Posizioni associate
            cur.execute("SELECT * FROM open_positions WHERE snapshot_id = ?;", (snapshot_id,))
            positions = []
            for row in cur.fetchall():
                positions.append({
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "size": row["size"],
                    "entry_price": row["entry_price"],
                    "mark_price": row["mark_price"],
                    "pnl_usd": row["pnl_usd"],
                    "leverage": row["leverage"]
                })
            data["positions"] = positions

        # Ultimo sentiment
        cur.execute("SELECT * FROM sentiment_contexts ORDER BY id DESC LIMIT 1;")
        sent = cur.fetchone()
        if sent:
            data["sentiment"] = {
                "value": sent["value"],
                "classification": sent["classification"],
                "timestamp": sent["sentiment_timestamp"]
            }

        # Ultime 15 operazioni
        cur.execute("SELECT * FROM bot_operations ORDER BY id DESC LIMIT 15;")
        ops = []
        for row in cur.fetchall():
            ops.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "operation": row["operation"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "target_portion_of_balance": row["target_portion_of_balance"],
                "leverage": row["leverage"],
                "raw_payload": row["raw_payload"]
            })
        data["operations"] = ops

        conn.close()
    except Exception as e:
        data["error"] = str(e)

    return data


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenzia i log standard di accesso per pulizia console

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            db_data = get_db_data()
            self.wfile.write(json.dumps(db_data).encode("utf-8"))
        elif parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy", "service": "dashboard"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/run":
            # Avvia main.py in background thread
            def run_main():
                subprocess.run(["python", "main.py"], check=False)

            threading.Thread(target=run_main, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "message": "Ciclo di trading avviato in background!"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def run_dashboard(port=PORT):
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"🌐 Dashboard Web attiva su http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_dashboard()
