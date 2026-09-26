from indicators import analyze_multiple_tickers
from news_feed import fetch_latest_news
from trading_agent import previsione_trading_agent, OPENROUTER_MODEL
from utils import check_stop_loss
from whalealert import format_whale_alerts_to_string
from sentiment import get_sentiment
from forecaster import get_crypto_forecasts
from synfutures_trader import SynFuturesTrader
from paper import PAPER_TRADING, PAPER_START_USDC, PaperSynFuturesTrader
import os
import json
import db_utils
import sys
from dotenv import load_dotenv

load_dotenv()

# Verifica stato pausa (da Coordinator o operatore)
if db_utils.is_bot_paused():
    pinfo = db_utils.get_pause_info()
    print(f"⏸️ Bot in stato di PAUSA ({pinfo.get('reason', 'Pausa attiva')}). Ciclo ignorato.")
    sys.exit(0)

# Configurazione SynFutures & OpenRouter
SYNFUTURES_WALLET = os.getenv("SYNFUTURES_WALLET") or os.getenv("WALLET_ADDRESS")
SYNFUTURES_PRIVATE_KEY = os.getenv("SYNFUTURES_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
SYNFUTURES_SERVICE_URL = os.getenv("SYNFUTURES_SERVICE_URL", "http://localhost:3100")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not SYNFUTURES_WALLET and not PAPER_TRADING:
    raise RuntimeError("SYNFUTURES_WALLET (o WALLET_ADDRESS) mancante nel .env")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY mancante nel .env")

try:
    if PAPER_TRADING:
        print(f"📝 PAPER TRADING: conto perpetual virtuale, prezzi reali. "
              f"Capitale iniziale ${PAPER_START_USDC:.2f}.")
        bot = PaperSynFuturesTrader()
    else:
        print(f"🚀 Avvio Intelligent Trading Agent per SynFutures (Wallet: {SYNFUTURES_WALLET})")
        bot = SynFuturesTrader(
            secret_key=SYNFUTURES_PRIVATE_KEY,
            account_address=SYNFUTURES_WALLET,
            service_url=SYNFUTURES_SERVICE_URL,
        )

    # 0. Auto-refuel USDC (se ETH disponibile oltre riserva gas, solo se non in paper)
    if not PAPER_TRADING:
        try:
            refuel_res = bot.ensure_usdc_balance()
            if refuel_res:
                print(f"⛽ Auto-refuel completato: {refuel_res.get('description', '')}")
        except Exception as exc:
            print(f"⚠️ Auto-refuel non riuscito (proseguo con saldo attuale): {exc}")

        # Saldo Wallet su Base (se disponibile) ed eventuale deposito automatico su Gate
        if getattr(bot, "client", None) and bot.client:
            try:
                eth_bal = bot.client.eth_balance()
                usdc_bal = bot.client.balance_of_float(config.USDC)
                print(f"💼 Saldo Wallet Base: {eth_bal:.5f} ETH | ${usdc_bal:.2f} USDC")

                if getattr(config, "AUTO_DEPOSIT_GATE", True):
                    min_dep = getattr(config, "MIN_GATE_DEPOSIT", 1.0)
                    if usdc_bal >= min_dep:
                        print(f"📥 Rilevati ${usdc_bal:.2f} USDC nel wallet. Deposito sul Gate SynFutures...")
                        dep_res = bot.deposit_usdc(usdc_bal)
                        print(f"✅ Deposito Gate completato: {dep_res}")
                        time.sleep(2)
            except Exception as exc:
                print(f"⚠️ Deposito automatico Gate non eseguito: {exc}")

    # Calcolo delle informazioni in input per Ticker: solo le coppie che
    # esistono davvero come perpetual su SynFutures
    candidates = [t.strip().upper() for t in os.getenv("TRADING_TICKERS", "BTC,ETH").split(",") if t.strip()]
    tickers = bot.filter_tradable(candidates)
    if not tickers:
        raise RuntimeError(f"Nessuno dei ticker {candidates} ha un perpetual su SynFutures")
    print(f"📊 Calcolo indicatori tecnici per: {tickers}...")
    indicators_txt, indicators_json = analyze_multiple_tickers(tickers)

    print("📰 Recupero ultime notizie di mercato...")
    news_txt = fetch_latest_news()

    print("🎭 Analisi sentiment Fear & Greed...")
    sentiment_txt, sentiment_json = get_sentiment()

    print("🔮 Calcolo previsioni con Prophet...")
    forecasts_txt, forecasts_json = get_crypto_forecasts(tickers)
    if not forecasts_txt:
        # Meglio dirlo esplicitamente all'LLM che passargli la stringa "None"
        forecasts_txt = "Previsioni non disponibili in questo ciclo."
        print("⚠️  Previsioni Prophet non disponibili: il modello deciderà senza forecast.")

    msg_info = f"""<indicatori>\n{indicators_txt}\n</indicatori>\n\n
    <news>\n{news_txt}</news>\n\n
    <sentiment>\n{sentiment_txt}\n</sentiment>\n\n
    <forecast>\n{forecasts_txt}\n</forecast>\n\n"""

    print("💰 Recupero stato conto e posizioni su SynFutures...")
    account_status = bot.get_account_status()
    print(f"   Saldo USD: ${account_status.get('balance_usd', 0.0):.2f}")
    print(f"   Posizioni aperte: {len(account_status.get('open_positions', []))}")
    if account_status.get("paper_trading"):
        paper = account_status.get("paper", {})
        print(f"   [paper] Equity ${paper.get('equity_usd', 0):.2f}, P&L ${paper.get('pnl_usd', 0):+.2f} "
              f"su ${paper.get('initial_usdc', 0):.2f} iniziali, {paper.get('trades', 0)} ordini simulati")

    stop_losses = check_stop_loss(account_status)
    portfolio_data = f"{json.dumps(account_status)}\n Stop Loss attivati 15 min fa: {stop_losses}"

    # Registra snapshot iniziale nel database (se configurato)
    try:
        snapshot_id = db_utils.log_account_status(account_status)
        print(f"[db_utils] Snapshot inserito con id={snapshot_id}")
    except Exception as db_err:
        print(f"[db_utils] Nota DB snapshot non salvato: {db_err}")

    # Creazione System prompt
    with open("system_prompt.txt", "r") as f:
        system_prompt_template = f.read()
    system_prompt = system_prompt_template.format(portfolio_data, msg_info)

    print(f"🤖 L'agente AI (OpenRouter {OPENROUTER_MODEL}) sta decidendo la sua azione...")
    out = previsione_trading_agent(system_prompt, symbols=tickers)
    print(f"   Segnale generato: {json.dumps(out, indent=2)}")

    print("⚡ Esecuzione del segnale su SynFutures...")
    execution_result = bot.execute_signal(out)
    print(f"   Risultato: {execution_result}")

    # Registra operazione nel database
    try:
        op_id = db_utils.log_bot_operation(
            out,
            system_prompt=system_prompt,
            indicators=indicators_json,
            news_text=news_txt,
            sentiment=sentiment_json,
            forecasts=forecasts_json,
        )
        print(f"[db_utils] Operazione inserita con id={op_id}")
    except Exception as db_err:
        print(f"[db_utils] Nota DB operazione non salvata: {db_err}")

    # Notifica su Telegram (se configurato)
    try:
        from telegram_bot import notify_cycle_result
        notify_cycle_result(
            decision=out,
            execution_result=execution_result,
            account_status=account_status,
            sentiment=sentiment_json,
            indicators=indicators_json,
            forecasts=forecasts_json,
        )
    except Exception as tg_err:
        print(f"[telegram] Nota notifica non inviata: {tg_err}")

    # Aggiorna e salva lo stato corrente
    account_status = bot.get_account_status()
    with open("account_status_old.json", "w") as f:
        json.dump(account_status.get("open_positions", []), f, indent=4)

    try:
        snapshot_id = db_utils.log_account_status(account_status)
        print(f"[db_utils] Snapshot finale inserito con id={snapshot_id}")
    except Exception:
        pass

    print("✅ Ciclo di trading completato con successo.")

except Exception as e:
    try:
        from telegram_bot import notify_error
        notify_error(str(e))
    except Exception:
        pass

    try:
        db_utils.log_error(
            e,
            context={
                "prompt": locals().get("system_prompt", ""),
                "tickers": locals().get("tickers", []),
                "indicators": locals().get("indicators_json", None),
                "news": locals().get("news_txt", ""),
                "sentiment": locals().get("sentiment_json", None),
                "forecasts": locals().get("forecasts_json", None),
                "balance": locals().get("account_status", None),
            },
            source="trading_agent",
        )
    except Exception:
        pass
    print(f"❌ Si è verificato un errore: {e}")
