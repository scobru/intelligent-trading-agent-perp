"""
Tool CLI di gestione del collaterale sul Gate di SynFutures V3 (Base Chain).

Permette di:
1. Verificare i saldi correnti (ETH per gas, USDC nel wallet, USDC sul Gate).
2. Eseguire il deposito sul Gate via microservizio REST o direttamente via Web3.
3. Eseguire il ritiro dal Gate nel proprio wallet.

Uso:
    python tools/deposit_gate.py --status
    python tools/deposit_gate.py --deposit --amount 50      # deposita 50 USDC sul Gate
    python tools/deposit_gate.py --withdraw --amount 25     # ritira 25 USDC dal Gate
    python tools/deposit_gate.py --deposit --amount 50 --dry-run
"""

import argparse
import logging
import os
import sys

# Aggiunge la root del progetto al path per importare moduli fratelli
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from base_client import BaseClient
from synfutures_trader import SynFuturesTrader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DepositGate")


def print_banner():
    print("=" * 65)
    print(" 🚪 SynFutures V3 Gate — Gestione Collaterale USDC (Base Chain)")
    print("=" * 65)


def get_status(trader: SynFuturesTrader, client: BaseClient):
    wallet = trader.account_address or client.address
    eth = client.eth_balance() if client and client.address else 0.0
    usdc_wallet = client.balance_of_float(config.USDC) if client and client.address else 0.0

    gate_usdc = 0.0
    gate_method = "service"
    try:
        status = trader.get_account_status()
        gate_usdc = float(status.get("balance_usd", 0.0))
    except Exception as exc:
        gate_method = "web3"
        try:
            gate_usdc = client.gate_balance_of_float(config.USDC)
        except Exception as e:
            logger.warning("Impossibile leggere Gate da Web3: %s", e)

    return {
        "wallet": wallet,
        "eth": eth,
        "usdc_wallet": usdc_wallet,
        "gate_usdc": gate_usdc,
        "gate_method": gate_method,
    }


def display_status(status: dict):
    print(f"📍 Wallet:               {status['wallet']}")
    print(f"⛽ ETH (Gas):            {status['eth']:.5f} ETH")
    print(f"💵 USDC nel Wallet:      ${status['usdc_wallet']:,.2f}")
    print(f"🚪 USDC sul Gate:        ${status['gate_usdc']:,.2f}")
    print("-" * 65)
    if status['gate_usdc'] > 0:
        print(f"✅ Saldo Gate disponibile: ${status['gate_usdc']:.2f} USDC.")
    else:
        print("⚠️ Saldo Gate vuoto ($0.00). Deposita USDC per aprire posizioni perpetual.")
    print("=" * 65)


def do_deposit(trader: SynFuturesTrader, client: BaseClient, amount: float, dry_run: bool = False):
    status = get_status(trader, client)
    display_status(status)

    if amount <= 0:
        print("❌ Specifica un importo valido da depositare con --amount <NUMERO>.")
        return

    print(f"\n🚀 Avvio deposito di ${amount:.2f} USDC sul Gate SynFutures...")
    if status["usdc_wallet"] < amount:
        print(f"❌ Errore: USDC insufficienti nel wallet (${status['usdc_wallet']:.2f} disponibili < ${amount:.2f} richiesti).")
        return

    if dry_run:
        print(f"[DRY-RUN] Simulazione completata: verrebbero depositati ${amount:.2f} USDC sul contratto Gate {config.SYNFUTURES_GATE}.")
        return

    try:
        res = trader.deposit_usdc(amount)
        print(f"✅ Deposito completato! Risultato: {res}")
    except Exception as exc:
        print(f"❌ Errore durante il deposito: {exc}")
        return

    new_status = get_status(trader, client)
    print(f"🎉 Nuovo saldo Gate: ${new_status['gate_usdc']:.2f} USDC.")


def do_withdraw(trader: SynFuturesTrader, client: BaseClient, amount: float, dry_run: bool = False):
    status = get_status(trader, client)
    display_status(status)

    if amount is None or amount <= 0:
        amount = status["gate_usdc"]

    if amount <= 0:
        print("❌ Nessun saldo disponibile sul Gate da ritirare.")
        return

    if amount > status["gate_usdc"]:
        print(f"❌ Errore: importo richiesto (${amount:.2f}) maggiore del saldo Gate (${status['gate_usdc']:.2f}).")
        return

    print(f"\n🚀 Avvio ritiro di ${amount:.2f} USDC dal Gate...")
    if dry_run:
        print(f"[DRY-RUN] Simulazione completata: verrebbero ritirati ${amount:.2f} USDC dal Gate nel wallet.")
        return

    try:
        res = trader.withdraw_usdc(amount)
        print(f"✅ Ritiro completato! Risultato: {res}")
    except Exception as exc:
        print(f"❌ Errore durante il ritiro: {exc}")
        return

    new_status = get_status(trader, client)
    print(f"🎉 Nuovo saldo Gate: ${new_status['gate_usdc']:.2f} USDC.")


def main():
    parser = argparse.ArgumentParser(description="Gestione collaterale Gate SynFutures V3 (Base)")
    parser.add_argument("--status", action="store_true", help="Mostra lo stato dei saldi wallet e Gate")
    parser.add_argument("--deposit", action="store_true", help="Deposita USDC sul Gate")
    parser.add_argument("--withdraw", action="store_true", help="Ritira USDC dal Gate nel wallet")
    parser.add_argument("--amount", type=float, default=None, help="Importo USDC da depositare o ritirare")
    parser.add_argument("--dry-run", action="store_true", help="Simula l'operazione senza inviare transazioni")
    args = parser.parse_args()

    print_banner()

    trader = SynFuturesTrader()
    client = trader.client or BaseClient()

    if args.dry_run:
        config.DRY_RUN = True

    if args.deposit:
        amount = args.amount if args.amount is not None else 0.0
        do_deposit(trader, client, amount, dry_run=args.dry_run)
    elif args.withdraw:
        do_withdraw(trader, client, args.amount, dry_run=args.dry_run)
    else:
        status = get_status(trader, client)
        display_status(status)
        print("\nSuggerimenti:")
        print(" • Per depositare fondi:  python tools/deposit_gate.py --deposit --amount 50")
        print(" • Per ritirare i fondi:   python tools/deposit_gate.py --withdraw --amount 25")
        print(" • Per simulare:           python tools/deposit_gate.py --deposit --amount 50 --dry-run")


if __name__ == "__main__":
    main()
