import os
import time
import json
import logging
import requests
import threading
import subprocess
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""


def is_configured() -> bool:
    """Verifica se il bot Telegram è configurato con token e chat id."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram_message(text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML") -> bool:
    """
    Invia un messaggio Telegram formattato in HTML.
    Utilizza HTML come default per evitare conflitti con caratteri speciali (come underscore o parentesi).
    """
    if not TELEGRAM_BOT_TOKEN:
        return False

    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not target_chat_id:
        logger.warning("[Telegram] Impossibile inviare: TELEGRAM_CHAT_ID non specificato.")
        return False

    url = f"{API_BASE_URL}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        res_json = resp.json()
        if not res_json.get("ok"):
            logger.warning(f"[Telegram] Errore API: {res_json.get('description')}")
            return False
        return True
    except Exception as e:
        logger.warning(f"[Telegram] Eccezione invio messaggio: {e}")
        return False


def notify_cycle_result(
    decision: Dict[str, Any],
    execution_result: Any,
    account_status: Optional[Dict[str, Any]] = None,
    sentiment: Optional[Dict[str, Any]] = None,
    indicators: Optional[Any] = None,
    forecasts: Optional[Any] = None,
):
    """Notifica il risultato dell'ultimo ciclo di trading e la decisione dell'AI."""
    if not is_configured():
        return

    op = str(decision.get("operazione", decision.get("operation", "hold"))).upper()
    sym = str(decision.get("simbolo", decision.get("symbol", ""))).upper()
    direction = str(decision.get("direzione", decision.get("direction", ""))).upper()
    portion = decision.get("porzione_target_saldo", decision.get("target_portion_of_balance", 0))
    leverage = decision.get("leva", decision.get("leverage", "1x"))
    reason = decision.get("motivo", decision.get("reason", "Nessuna motivazione salvata."))

    # Emoji in base all'azione
    if op == "OPEN":
        action_icon = "🟢" if direction == "LONG" else "🔴"
        action_title = f"{action_icon} <b>NUOVO ORDINE: {op} {direction} ({sym})</b>"
    elif op == "CLOSE":
        action_title = f"🟡 <b>CHIUSURA POSIZIONE: {sym}</b>"
    else:
        action_title = "⏸️ <b>DECISIONE: HOLD (Nessuna operazione)</b>"

    lines = [
        "🤖 <b>Intelligent Trading Agent • Report Ciclo</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        action_title,
    ]

    if account_status and account_status.get("paper_trading"):
        pnl = account_status.get("pnl_since_start_usd", 0.0)
        lines.append(f"📝 <i>PAPER TRADING — P&amp;L strategia ${pnl:+.2f}</i>")

    if op in ["OPEN", "CLOSE"]:
        lines.append(f"📊 <b>Allocazione:</b> {float(portion)*100:.0f}%  |  <b>Leva:</b> {leverage}")
        if execution_result:
            exec_str = str(execution_result)
            if len(exec_str) > 100:
                exec_str = exec_str[:100] + "..."
            lines.append(f"⚡ <b>Risultato:</b> <code>{exec_str}</code>")

    # Stato Conto
    if account_status:
        balance = account_status.get("balance_usd", 0.0)
        positions = account_status.get("open_positions", [])
        lines.append("")
        label = "Collaterale virtuale" if account_status.get("paper_trading") else "Saldo Gate"
        lines.append(f"💰 <b>{label}:</b> ${float(balance):.2f} USDC")
        eth = account_status.get("wallet_eth_balance")
        if eth is not None and not account_status.get("paper_trading"):
            warn = float(os.getenv("GAS_WARN_ETH", "0.002"))
            flag = " ⚠️ <b>ricarica il wallet</b>" if eth < warn else ""
            lines.append(f"⛽ <b>ETH per il gas:</b> {eth:.5f}{flag}")
        if account_status.get("total_value_usd") is not None:
            lines.append(f"💼 <b>Valore totale:</b> ${float(account_status['total_value_usd']):.2f}")
        lines.append(f"📈 <b>Posizioni Aperte:</b> {len(positions)}")

    # Sentiment
    if sentiment:
        val = sentiment.get("valore", sentiment.get("value", "--"))
        cls_name = sentiment.get("classificazione", sentiment.get("classification", ""))
        lines.append(f"🎭 <b>Fear & Greed:</b> {val}/100 ({cls_name})")

    # Spiegazione AI
    lines.append("")
    lines.append("🧠 <b>AI Rationale:</b>")
    lines.append(f"<i>{reason}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    msg = "\n".join(lines)
    send_telegram_message(msg)


def notify_error(error_msg: str):
    """Invia un avviso in caso di errore durante l'esecuzione del ciclo."""
    if not is_configured():
        return

    text = (
        "⚠️ <b>Allarme Intelligent Trading Agent</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Si è verificato un errore durante il ciclo di trading:\n"
        f"<code>{error_msg}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Il bot riproverà automaticamente al prossimo intervallo schedulato.</i>"
    )
    send_telegram_message(text)


# ==============================================================================
# TELEGRAM BOT POLLING LISTENER (COMANDI INTERATTIVI)
# ==============================================================================

def _handle_command(text: str, chat_id: str):
    """Elabora i comandi inviati dall'utente al bot Telegram."""
    cmd = text.strip().split()[0].lower()

    if cmd in ["/start", "/help"]:
        help_msg = (
            "🤖 <b>Intelligent Trading Agent Bot</b>\n\n"
            "Ecco i comandi disponibili:\n"
            "• /status - Stato del bot, saldo e posizioni attive\n"
            "• /positions - Dettaglio delle posizioni aperte e PnL\n"
            "• /run - Forza l'esecuzione immediata di un ciclo di trading\n"
            "• /sentiment - Indice Fear & Greed attuale\n"
            "• /signals - Ultimo segnale e motivazione dell'AI\n"
            "• /help - Mostra questo messaggio di aiuto\n\n"
            "<i>Il bot opera automaticamente ogni 15 minuti su SynFutures (Base DEX).</i>"
        )
        send_telegram_message(help_msg, chat_id=chat_id)

    elif cmd == "/status":
        try:
            from dashboard import get_db_data
            data = get_db_data()
            bal = data.get("balance", 0.0)
            pos_count = len(data.get("positions", []))
            sent = data.get("sentiment", {})
            sent_val = sent.get("value", "--")
            sent_cls = sent.get("classification", "")
            last_op = data.get("operations", [{}])[0] if data.get("operations") else {}
            op_name = last_op.get("operation", "Nessuna").upper()
            op_sym = last_op.get("symbol", "")

            msg = (
                "📊 <b>Stato Trading Agent</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Saldo:</b> ${bal:.2f} USDC\n"
                f"📈 <b>Posizioni Aperte:</b> {pos_count}\n"
                f"🎭 <b>Sentiment:</b> {sent_val}/100 ({sent_cls})\n"
                f"⚡ <b>Ultima Operazione:</b> {op_name} {op_sym}\n"
                f"🕒 <b>Data:</b> {last_op.get('created_at', '--')}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            send_telegram_message(msg, chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Errore recupero stato: {e}", chat_id=chat_id)

    elif cmd == "/positions":
        try:
            from dashboard import get_db_data
            data = get_db_data()
            positions = data.get("positions", [])
            if not positions:
                send_telegram_message("ℹ️ Nessuna posizione aperta al momento su SynFutures.", chat_id=chat_id)
                return

            lines = ["📊 <b>Posizioni Aperte (SynFutures):</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
            for p in positions:
                side_icon = "🟢" if p.get("side", "").lower() == "long" else "🔴"
                pnl = float(p.get("pnl_usd", 0.0))
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                lines.append(
                    f"{side_icon} <b>{p.get('symbol')}</b> ({p.get('side', '').upper()})\n"
                    f"   Quantità: {p.get('size')} | Leva: {p.get('leverage')}\n"
                    f"   Entry: ${float(p.get('entry_price', 0)):.2f} | Mark: ${float(p.get('mark_price', 0)):.2f}\n"
                    f"   PnL: <b>{pnl_str}</b>\n"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            send_telegram_message("\n".join(lines), chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Errore lettura posizioni: {e}", chat_id=chat_id)

    elif cmd == "/sentiment":
        try:
            from sentiment import get_latest_fear_and_greed
            sent = get_latest_fear_and_greed()
            val = sent.get("valore", 50)
            cls_name = sent.get("classificazione", "Neutral")
            icon = "🟢" if val >= 60 else ("🔴" if val <= 40 else "🟡")
            msg = (
                f"🎭 <b>Crypto Fear & Greed Index</b>\n\n"
                f"{icon} <b>Punteggio:</b> {val} / 100\n"
                f"🏷️ <b>Classificazione:</b> {cls_name.upper()}\n"
                f"📡 <i>Fonte: Alternative.me (free)</i>"
            )
            send_telegram_message(msg, chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Errore sentiment: {e}", chat_id=chat_id)

    elif cmd == "/signals":
        try:
            from dashboard import get_db_data
            data = get_db_data()
            ops = data.get("operations", [])
            if not ops:
                send_telegram_message("ℹ️ Nessun segnale salvato.", chat_id=chat_id)
                return

            last = ops[0]
            reason = "Nessuna spiegazione salvata."
            try:
                payload = json.loads(last.get("raw_payload", "{}"))
                reason = payload.get("reason", reason)
            except Exception:
                pass

            msg = (
                "🧠 <b>Ultimo Segnale AI</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Azione:</b> {last.get('operation', '').upper()} {last.get('symbol', '')} ({last.get('direction', '').upper()})\n"
                f"📊 <b>Leva:</b> {last.get('leverage', '--')} | <b>Allocazione:</b> {last.get('target_portion_of_balance', '--')}\n"
                f"🕒 <b>Registrato:</b> {last.get('created_at', '--')}\n\n"
                f"💡 <b>Ragionamento:</b>\n<i>{reason}</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            send_telegram_message(msg, chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Errore segnali: {e}", chat_id=chat_id)

    elif cmd == "/run":
        send_telegram_message("⚡ <b>Avvio ciclo di trading...</b>\nL'AI sta analizzando i mercati e riceverai il report a breve!", chat_id=chat_id)
        def _run_script():
            subprocess.run(["python", "main.py"], check=False)
        threading.Thread(target=_run_script, daemon=True).start()

    else:
        send_telegram_message("Comando non riconosciuto. Usa /help per la lista comandi.", chat_id=chat_id)


def run_telegram_listener():
    """
    Loop continuo di polling per ascoltare i messaggi in arrivo.
    Eseguibile in background nel container o come processo separato.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.info("[Telegram] TELEGRAM_BOT_TOKEN non impostato. Listener disattivato.")
        return

    print("📱 Telegram Bot Listener avviato. In attesa di comandi...")
    offset = 0

    while True:
        try:
            url = f"{API_BASE_URL}/getUpdates"
            params = {"offset": offset, "timeout": 25}
            resp = requests.get(url, params=params, timeout=35)
            data = resp.json()

            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if msg and "text" in msg:
                        text = msg["text"]
                        chat_id = str(msg["chat"]["id"])
                        _handle_command(text, chat_id)
            else:
                time.sleep(3)
        except Exception as e:
            # Attendi qualche secondo prima di riprovare in caso di disconnessione di rete
            time.sleep(5)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN non configurato nel .env. Inserisci il token per avviare il bot.")
    else:
        run_telegram_listener()
