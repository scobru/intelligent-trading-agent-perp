"""
Watchdog stop loss: SynFutures non ha ordini di stop on-chain, quindi tra un ciclo
dell'agente (15 min) e l'altro le posizioni non sarebbero protette.
Questo processo controlla le posizioni ogni WATCHDOG_INTERVAL_SECONDS e chiude
quelle che superano lo stop_loss_percent salvato all'apertura.
"""
import os
import time
from dotenv import load_dotenv

import config
from synfutures_trader import SynFuturesTrader

load_dotenv()


def main():
    interval = config.WATCHDOG_INTERVAL_SECONDS
    if interval <= 0:
        print("[watchdog] disattivato (WATCHDOG_INTERVAL_SECONDS=0)")
        return
    bot = SynFuturesTrader(
        secret_key=config.PRIVATE_KEY,
        account_address=config.WALLET_ADDRESS,
        service_url=config.SYNFUTURES_SERVICE_URL,
    )
    print(f"[watchdog] attivo, controllo stop loss ogni {interval}s")
    while True:
        try:
            for c in bot.enforce_stop_losses():
                print(f"[watchdog] 🛑 chiusa {c['symbol']} ({c['side']}), mossa avversa {c['adverse_percent']}%, PnL ${c['pnl_usd']}")
        except Exception as exc:
            print(f"[watchdog] errore: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
