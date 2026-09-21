from synfutures_trader import SynFuturesTrader
import os
from dotenv import load_dotenv
import json
import time

load_dotenv()

# -------------------------------------------------------------------
#                    CONFIG PANEL
# -------------------------------------------------------------------
SYNFUTURES_WALLET = os.getenv("SYNFUTURES_WALLET") or os.getenv("WALLET_ADDRESS")
SYNFUTURES_PRIVATE_KEY = os.getenv("SYNFUTURES_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
SYNFUTURES_SERVICE_URL = os.getenv("SYNFUTURES_SERVICE_URL", "http://localhost:3100")

if not SYNFUTURES_WALLET:
    raise RuntimeError("SYNFUTURES_WALLET o WALLET_ADDRESS mancanti nel .env")

# -------------------------------------------------------------------
#                    INIT BOT
# -------------------------------------------------------------------
print("🔄 Inizializzazione SynFuturesTrader...")
bot = SynFuturesTrader(
    secret_key=SYNFUTURES_PRIVATE_KEY,
    account_address=SYNFUTURES_WALLET,
    service_url=SYNFUTURES_SERVICE_URL,
)

def pretty(obj):
    return json.dumps(obj, indent=2)

bot.debug_symbol_limits("BTC")

# Prima del test
print(f"🔧 Leva/stato corrente per BTC: {pretty(bot.get_current_leverage('BTC'))}")

# Stato iniziale
status = bot.get_account_status()
print(f"💰 Stato account iniziale:\n{pretty(status)}")

if status["open_positions"]:
    pos = status["open_positions"][0]
    print(f"📊 Posizione aperta rilevata: {pos['size']} {pos['symbol']} con leva {pos.get('leverage', 'N/A')}")

print("\n---------------------------------------------------")
print("🔄 Testing SynFuturesTrader Signal Execution")
print("---------------------------------------------------\n")

# -------------------------------------------------------------------
#                    TEST 1 — HOLD ORDER
# -------------------------------------------------------------------
signal_hold = {
    "operation": "hold",
    "symbol": "BTC",
    "direction": "long",
    "target_portion_of_balance": 0.05,
    "leverage": 2,
    "stop_loss_percent": 2,
    "reason": "Test segnale HOLD su BTC"
}

print("📌 TEST 1 — HOLD ORDER")
try:
    result_hold = bot.execute_signal(signal_hold)
    print("Risultato HOLD:\n", pretty(result_hold))
except Exception as e:
    print("❌ ERRORE durante HOLD:", e)

# -------------------------------------------------------------------
#                    TEST 2 — OPEN ORDER (SIMULATO/REALE)
# -------------------------------------------------------------------
signal_open = {
    "operation": "open",
    "symbol": "BTC",
    "direction": "long",
    "target_portion_of_balance": 0.05,
    "leverage": 3,
    "stop_loss_percent": 2,
    "reason": "Test apertura posizione long BTC"
}

print("\n📌 TEST 2 — OPEN ORDER (BTC LONG)")
print("Nota: L'ordine viene inviato a SynFutures via synfutures-service (Base chain)")
try:
    result_open = bot.execute_signal(signal_open)
    print("Risultato OPEN:\n", pretty(result_open))
except Exception as e:
    print("❌ ERRORE o avviso durante apertura:", e)

print("\n💰 Stato account aggiornato:")
try:
    print(pretty(bot.get_account_status()))
except Exception as e:
    print("❌ Errore recuperando account status:", e)

print("\n---------------------------------------------------")
print("🏁 Testing SynFuturesTrader completato.")
print("---------------------------------------------------\n")
