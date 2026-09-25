import os
import json
import logging
import time
import requests
import time
from typing import Dict, Any, List, Optional
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv

import config
from base_client import BaseClient, BaseChainError
from uniswap import UniswapV3

load_dotenv()

logger = logging.getLogger("SynFuturesTrader")

class SynFuturesTrader:
    """
    Exchange Trader per SynFutures V3 (Base Chain).
    Fornisce un'interfaccia compatibile con HyperLiquidTrader per
    la gestione delle posizioni, balance ed esecuzione dei segnali AI.
    Comunica tramite REST con il microservizio synfutures-service (Node.js/Oyster SDK)
    e direttamente on-chain via Web3 per auto-refuel USDC e deposito sul Gate.
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        account_address: Optional[str] = None,
        service_url: Optional[str] = None,
        api_key: Optional[str] = None,
        testnet: bool = False,
        timeout: int = 60,
    ):
        self.secret_key = secret_key or os.getenv("SYNFUTURES_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
        self.account_address = account_address or os.getenv("SYNFUTURES_WALLET") or os.getenv("WALLET_ADDRESS")
        self.service_url = (
            service_url
            or os.getenv("SYNFUTURES_SERVICE_URL")
            or "http://localhost:3100"
        ).rstrip("/")
        self.api_key = api_key or os.getenv("API_KEY")
        self.timeout = timeout
        self.testnet = testnet
        self._markets: Dict[str, str] = {}
        self._markets_at = 0.0

        # Client Web3 su Base (per auto-refuel da ETH e fallback sul Gate)
        self.client: Optional[BaseClient] = None
        self._uniswap: Optional[UniswapV3] = None
        try:
            rpc_url = os.getenv("BASE_RPC") or os.getenv("BASE_RPC_URL") or config.BASE_RPC_URL
            self.client = BaseClient(
                rpc_url=rpc_url,
                private_key=self.secret_key,
                address=self.account_address,
            )
            if self.client.address and not self.account_address:
                self.account_address = self.client.address
        except Exception as e:
            logger.warning(f"⚠️ Impossibile inizializzare BaseClient Web3: {e}")

        # Verifica preliminare raggiungibilità del servizio
        self._check_service_health()

    @property
    def uniswap(self) -> Optional[UniswapV3]:
        if self._uniswap is None and self.client:
            self._uniswap = UniswapV3(self.client)
        return self._uniswap

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.service_url}{endpoint}"
        headers = self._get_headers()
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            elif method.upper() == "POST":
                resp = requests.post(url, headers=headers, json=data, timeout=self.timeout)
            else:
                raise ValueError(f"Metodo HTTP non supportato: {method}")

            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Impossibile connettersi a synfutures-service su {url}. Il servizio è attivo?")
            raise RuntimeError(f"SynFutures microservice non raggiungibile: {e}")
        except requests.exceptions.HTTPError as e:
            err_msg = str(e)
            try:
                err_json = e.response.json()
                if "error" in err_json:
                    err_msg = err_json["error"]
            except Exception:
                pass
            logger.error(f"❌ Errore API SynFutures ({endpoint}): {err_msg}")
            raise RuntimeError(f"SynFutures API error: {err_msg}")
        except Exception as e:
            logger.error(f"❌ Richiesta a {url} fallita: {e}")
            raise

    def _check_service_health(self):
        try:
            health = self._make_request("GET", "/health")
            if not self.account_address and health.get("signerAddress"):
                self.account_address = health.get("signerAddress")
            logger.info(f"✅ SynFutures service connesso: {health}")
        except Exception as e:
            logger.warning(f"⚠️ synfutures-service non risponde al momento: {e}")

    # ----------------------------------------------------------------------
    #                        NORMALIZZAZIONE SIMBOLI
    # ----------------------------------------------------------------------
    # ----------------------------------------------------------------------
    #                   WALLET (gas e USDC fuori dal Gate)
    # ----------------------------------------------------------------------
    USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    def _rpc(self, method: str, params: list) -> Any:
        url = os.getenv("BASE_RPC") or os.getenv("BASE_RPC_URL") or "https://mainnet.base.org"
        resp = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                             timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(body["error"])
        return body["result"]

    def get_wallet_balances(self, address: str) -> Dict[str, Any]:
        """
        ETH (per il gas) e USDC liberi nel wallet del signer, letti da RPC.
        Servono a sapere quando ricaricare: senza ETH le transazioni verso
        SynFutures falliscono. Errori di rete -> dizionario vuoto.
        """
        out: Dict[str, Any] = {}
        try:
            out["wallet_eth_balance"] = int(self._rpc("eth_getBalance", [address, "latest"]), 16) / 1e18
            data = "0x70a08231" + address.lower().replace("0x", "").rjust(64, "0")  # balanceOf(address)
            raw = self._rpc("eth_call", [{"to": self.USDC_BASE, "data": data}, "latest"])
            out["wallet_usdc_balance"] = int(raw, 16) / 1e6
        except Exception as e:
            logger.warning(f"⚠️ Saldo del wallet non disponibile: {e}")
        return out

    # Expiry dei perpetual in SynFutures V3 (type(uint32).max)
    PERP_EXPIRY = "4294967295"
    MARKETS_TTL = 600

    def get_tradable_markets(self) -> Dict[str, str]:
        """
        Mercati perpetual realmente quotati su SynFutures: ticker -> simbolo
        dello strumento (es. {"BTC": "BTC-USDC-LINK"}). Serve a non chiedere
        ordini su coppie che non esistono.
        Cache di 10 minuti; se il servizio non risponde restituisce {} e i
        chiamanti ripiegano sul comportamento precedente.
        """
        if self._markets and time.time() - self._markets_at < self.MARKETS_TTL:
            return self._markets
        try:
            instruments = self._make_request("GET", "/instruments")
        except Exception as e:
            logger.warning(f"⚠️ Elenco strumenti SynFutures non disponibile: {e}")
            return self._markets

        markets: Dict[str, str] = {}
        for inst in instruments if isinstance(instruments, list) else []:
            sym = str(inst.get("symbol") or "").upper()
            parts = sym.split("-")
            if len(parts) < 2:
                continue
            amms = inst.get("amms") or {}
            if amms and self.PERP_EXPIRY not in amms:
                continue  # nessun perpetual su questo strumento
            base, quote = parts[0], parts[1]
            if quote not in ("USDC", "USDB", "USDT"):
                continue
            # a parita' di base preferisci il margine in USDC
            if base not in markets or (quote == "USDC" and not markets[base].split("-")[1] == "USDC"):
                markets[base] = inst.get("symbol")
        if markets:
            self._markets, self._markets_at = markets, time.time()
        return self._markets

    def filter_tradable(self, tickers: List[str]) -> List[str]:
        """Tiene solo i ticker che hanno un perpetual su SynFutures."""
        markets = self.get_tradable_markets()
        if not markets:
            return list(tickers)  # elenco non disponibile: non scartare nulla
        kept = [t for t in tickers if t.upper() in markets]
        skipped = [t for t in tickers if t.upper() not in markets]
        if skipped:
            logger.warning(f"⚠️ Nessun perpetual SynFutures per {skipped}: esclusi dal ciclo")
            print(f"⚠️  Nessun perpetual SynFutures per {', '.join(skipped)}: esclusi dal ciclo")
        return kept

    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalizza il simbolo nel formato SynFutures Base-Quote-Oracle (es: BTC-USDC-LINK).
        Se lo strumento esiste, usa il simbolo reale restituito dal servizio.
        """
        sym = str(symbol or "").strip().upper().replace("/", "-").replace("_", "-")
        base = sym.split("-")[0]
        if base and base in self._markets:
            return self._markets[base]
        parts = sym.split("-")

        # Se passato solo il nome del coin (es. "BTC")
        if len(parts) == 1:
            coin = parts[0]
            return f"{coin}-USDC-LINK"
        elif len(parts) == 2:
            # es. BTC-USDT o BTC-USDC -> aggiungi default oracle LINK
            base = parts[0]
            quote = "USDC" if parts[1] in ("USDT", "USDC", "USD") else parts[1]
            return f"{base}-{quote}-LINK"
        elif len(parts) >= 3:
            # es. BTC-USDT-LINK -> converti USDT in USDC
            return f"{parts[0]}-USDC-{parts[2]}"
        return sym

    def denormalize_symbol(self, synfutures_symbol: str) -> str:
        """
        Estrae il ticker base (es. 'BTC-USDC-LINK' -> 'BTC').
        """
        parts = synfutures_symbol.split("-")
        return parts[0] if len(parts) > 0 else synfutures_symbol

    # ----------------------------------------------------------------------
    #                            VALIDAZIONE INPUT
    # ----------------------------------------------------------------------
    def _validate_order_input(self, order_json: Dict[str, Any]):
        required_fields = [
            "operation",
            "symbol",
            "target_portion_of_balance",
            "leverage",
            "reason",
        ]
        for f in required_fields:
            if f not in order_json:
                raise ValueError(f"Missing required field: {f}")

        if not isinstance(order_json["symbol"], str) or not order_json["symbol"].strip():
            raise ValueError("symbol must be a non-empty string")

        order_json["operation"] = str(order_json["operation"] or "").strip().lower()
        if order_json["operation"] not in ("open", "close", "hold"):
            raise ValueError("operation must be 'open', 'close', or 'hold'")

        # La direzione conta solo per aprire: close chiude la posizione del
        # simbolo qualunque sia il lato, hold non fa nulla
        direction = str(order_json.get("direction") or "").strip().lower()
        if direction in ("long", "short"):
            order_json["direction"] = direction
        elif order_json["operation"] == "open":
            raise ValueError("direction must be 'long' or 'short'")

        try:
            float(order_json["target_portion_of_balance"])
        except Exception:
            raise ValueError("target_portion_of_balance must be a number")

    # ----------------------------------------------------------------------
    #                        STATO ACCOUNT & POSIZIONI
    # ----------------------------------------------------------------------
    def get_account_status(self) -> Dict[str, Any]:
        """
        Recupera il saldo Gate (USDC) e le posizioni aperte su SynFutures.
        Restituisce un dizionario conforme allo schema di HyperLiquidTrader:
        {
            "balance_usd": float,
            "open_positions": [
                {
                    "symbol": "BTC",
                    "side": "long" / "short",
                    "size": float,
                    "entry_price": float,
                    "mark_price": float,
                    "pnl_usd": float,
                    "leverage": "3x"
                }
            ]
        }
        """
        # 1. Recupera indirizzo se non già memorizzato
        address = self.account_address
        if not address:
            health = self._make_request("GET", "/health")
            address = health.get("signerAddress")
            if not address:
                raise RuntimeError("Nessun indirizzo wallet configurato in SynFutures service")
            self.account_address = address

        # 2. Recupera Saldo Gate
        balances = self._make_request("GET", f"/gate/balance/{address}")
        total_usd = 0.0
        if isinstance(balances, list):
            for b in balances:
                if b.get("symbol", "").upper() in ["USDC", "USDT", "USD", "USDB", "DAI"]:
                    try:
                        total_usd += float(b.get("balance", 0))
                    except (ValueError, TypeError):
                        pass

        # 3. Recupera Posizioni Portafoglio
        portfolios = self._make_request("GET", f"/portfolio/{address}")
        open_positions = []

        if isinstance(portfolios, list):
            for item in portfolios:
                pos = item.get("position", {})
                size_raw = pos.get("size", "0")
                try:
                    size = float(size_raw) / 1e18 if size_raw else 0.0
                except (ValueError, TypeError):
                    size = 0.0

                if abs(size) > 0.0001:
                    raw_symbol = item.get("symbol", "")
                    symbol = self.denormalize_symbol(raw_symbol)

                    try:
                        entry_price = float(pos.get("entryPrice", "0")) / 1e18
                    except Exception:
                        entry_price = 0.0

                    try:
                        mark_price = float(pos.get("markPrice", "0")) / 1e18
                    except Exception:
                        mark_price = 0.0

                    # Calcolo PnL
                    side_raw = pos.get("side", 0)
                    # 1 = SHORT, 2 = LONG
                    is_long = (side_raw == 2 or str(side_raw).upper() == "LONG" or size > 0)
                    side_str = "long" if is_long else "short"

                    pnl = (mark_price - entry_price) * size

                    # Stima leva o margin
                    margin_raw = pos.get("margin", "0")
                    try:
                        margin_val = float(margin_raw) / 1e18
                    except Exception:
                        margin_val = 0.0
                    
                    notional = mark_price * abs(size)
                    leverage_str = f"{round(notional / margin_val, 1)}x" if margin_val > 0 else "cross"

                    open_positions.append({
                        "symbol": symbol,
                        "side": side_str,
                        "size": abs(size),
                        "entry_price": round(entry_price, 4),
                        "mark_price": round(mark_price, 4),
                        "pnl_usd": round(pnl, 4),
                        "leverage": leverage_str,
                        "margin_usd": round(margin_val, 2),
                    })

        # Valore complessivo: Gate + margine e P&L non realizzato delle posizioni
        total_value = total_usd + sum(p["margin_usd"] + p["pnl_usd"] for p in open_positions)
        return {
            "balance_usd": round(total_usd, 2),
            "total_value_usd": round(total_value, 2),
            "open_positions": open_positions,
            "mode": "live",
            "wallet_address": address,
            **self.get_wallet_balances(address),
        }

    # ----------------------------------------------------------------------
    #                   GESTIONE GATE & AUTO-REFUEL
    # ----------------------------------------------------------------------
    def deposit_usdc(self, amount: float) -> Dict[str, Any]:
        """
        Deposita USDC sul Gate di SynFutures.
        Usa primariamente il microservizio REST (synfutures-service); se non risponde,
        fa fallback sulla chiamata diretta on-chain Web3 (BaseClient.deposit_to_gate).
        """
        if amount <= 0:
            raise ValueError("Importo di deposito non valido")

        # 1. Tentativo via microservizio REST
        try:
            logger.info(f"⏳ Deposito di ${amount:.2f} USDC sul Gate via synfutures-service...")
            res = self._make_request("POST", "/gate/deposit", {"token": "USDC", "amount": f"{amount:.6f}"})
            logger.info(f"✅ Deposito Gate completato via microservizio: {res}")
            return res
        except Exception as err:
            logger.warning(f"⚠️ Deposito via microservizio fallito ({err}). Tentativo fallback Web3 diretto...")

        # 2. Fallback Web3 diretto
        if self.client and self.client.has_signer:
            res = self.client.deposit_to_gate(config.USDC, amount)
            logger.info(f"✅ Deposito Gate completato via Web3: {res}")
            return res

        raise RuntimeError(f"Impossibile depositare ${amount:.2f} USDC sul Gate (servizio e Web3 falliti)")

    def withdraw_usdc(self, amount: float) -> Dict[str, Any]:
        """
        Ritira USDC dal Gate di SynFutures verso il wallet.
        """
        if amount <= 0:
            raise ValueError("Importo di ritiro non valido")

        try:
            logger.info(f"⏳ Ritiro di ${amount:.2f} USDC dal Gate via synfutures-service...")
            res = self._make_request("POST", "/gate/withdraw", {"token": "USDC", "amount": f"{amount:.6f}"})
            logger.info(f"✅ Ritiro Gate completato via microservizio: {res}")
            return res
        except Exception as err:
            logger.warning(f"⚠️ Ritiro via microservizio fallito ({err}). Tentativo fallback Web3...")

        if self.client and self.client.has_signer:
            res = self.client.withdraw_from_gate(config.USDC, amount)
            logger.info(f"✅ Ritiro Gate completato via Web3: {res}")
            return res

        raise RuntimeError(f"Impossibile ritirare ${amount:.2f} USDC dal Gate")

    def release_funds(self, target_usdc: float = 0.0) -> Dict[str, Any]:
        """
        Chiude posizioni se necessario e ritira USDC dal Gate di SynFutures verso il wallet Base.
        Se target_usdc <= 0, ritira tutto il saldo disponibile sul Gate (e chiude posizioni aperte).
        """
        status = self.get_account_status()
        gate_balance = float(status.get("balance_usd", 0.0))
        open_positions = list(status.get("open_positions", []))

        closed_positions = []
        # 1. Se il saldo libero sul Gate è inferiore all'importo richiesto e ci sono posizioni aperte,
        # chiudile per liberare margine
        should_close = (target_usdc <= 0 and open_positions) or (target_usdc > 0 and gate_balance < target_usdc and open_positions)
        if should_close:
            for pos in open_positions:
                sym = pos.get("symbol")
                if sym:
                    try:
                        logger.info(f"[SynFuturesTrader] Chiusura posizione {sym} per liberare fondi...")
                        norm_sym = self.normalize_symbol(sym)
                        c_res = self._make_request("POST", "/order/close", {"symbol": norm_sym})
                        closed_positions.append({"symbol": sym, "result": c_res})
                    except Exception as err:
                        logger.warning(f"Errore chiusura posizione {sym}: {err}")
            time.sleep(2)
            status = self.get_account_status()
            gate_balance = float(status.get("balance_usd", 0.0))

        # 2. Ritira USDC dal Gate verso il wallet Base L2
        to_withdraw = gate_balance if target_usdc <= 0 else min(target_usdc, gate_balance)
        withdrawn = 0.0
        min_withdraw = 0.50
        if to_withdraw >= min_withdraw:
            try:
                logger.info(f"[SynFuturesTrader] Ritiro di ${to_withdraw:.2f} USDC dal Gate al wallet...")
                self.withdraw_usdc(to_withdraw)
                withdrawn = to_withdraw
            except Exception as w_err:
                logger.error(f"Errore prelievo dal Gate: {w_err}")
                return {
                    "status": "error",
                    "message": f"Prelievo dal Gate non riuscito: {w_err}",
                    "closed_positions": closed_positions,
                    "gate_balance": gate_balance
                }

        wallet_usdc = 0.0
        if self.client:
            wallet_usdc = self.client.balance_of_float(config.USDC)

        return {
            "status": "success",
            "withdrawn_usd": round(withdrawn, 2),
            "target_requested": target_usdc,
            "closed_positions": closed_positions,
            "gate_balance": round(gate_balance - withdrawn, 2),
            "wallet_usdc": round(wallet_usdc, 2),
            "message": f"Ritirati ${withdrawn:.2f} USDC dal Gate (saldo wallet: ${wallet_usdc:.2f})"
        }

    def ensure_usdc_balance(self) -> Optional[Dict[str, Any]]:
        """
        Auto-refuel: Se il saldo USDC nel wallet e' sotto soglia ma c'e' ETH spendibile,
        swappa in automatico ETH in USDC su Uniswap V3 (Base).
        """
        if not getattr(config, "AUTO_SWAP_ETH_TO_USDC", True) or not self.uniswap:
            return None
        return self.uniswap.auto_refuel_usdc()

    def ensure_gate_margin(self, required_margin_usd: float) -> Dict[str, Any]:
        """
        Verifica il margine disponibile su Gate rispetto a required_margin_usd.
        Se insufficiente:
        1. Esegue auto-refuel ETH -> USDC su Uniswap V3 se il wallet e' a corto di USDC
        2. Deposita il margine mancante (shortfall) sul Gate di SynFutures
        """
        status = self.get_account_status()
        gate_balance = float(status.get("balance_usd", 0.0))

        # Applica buffer di sicurezza per coprire funding e fee
        buffer_factor = getattr(config, "GATE_MARGIN_BUFFER", 1.20)
        needed = required_margin_usd * buffer_factor
        shortfall = max(0.0, needed - gate_balance)

        if shortfall <= 0.01:
            logger.info(f"✅ Margine Gate sufficiente: ${gate_balance:.2f} >= ${needed:.2f}")
            return {"status": "ok", "deposited": 0.0, "gate_balance": gate_balance}

        logger.info(
            f"⚠️ Fabbisogno Gate: servono ${needed:.2f} USDC (Gate attuale: ${gate_balance:.2f}, shortfall: ${shortfall:.2f})"
        )

        # 1. Controlla wallet USDC ed eventuale auto-refuel da ETH
        wallet_usdc = 0.0
        if self.client:
            wallet_usdc = self.client.balance_of_float(config.USDC)
            if wallet_usdc < shortfall and getattr(config, "AUTO_SWAP_ETH_TO_USDC", True):
                logger.info(f"⛽ USDC nel wallet (${wallet_usdc:.2f}) < fabbisogno (${shortfall:.2f}). Tentativo auto-refuel da ETH...")
                refuel_res = self.ensure_usdc_balance()
                if refuel_res:
                    logger.info(f"✅ Auto-refuel completato: {refuel_res.get('description', '')}")
                    wallet_usdc = self.client.balance_of_float(config.USDC)

        # 2. Deposita su Gate se abilitato
        if not getattr(config, "AUTO_DEPOSIT_GATE", True):
            logger.warning("AUTO_DEPOSIT_GATE disattivato. Salto il deposito automatico sul Gate.")
            return {"status": "skipped", "deposited": 0.0, "gate_balance": gate_balance}

        # Calcola importo effettivo da depositare
        if wallet_usdc <= 0:
            logger.warning("⚠️ Saldo USDC nel wallet pari a zero: impossibile depositare sul Gate.")
            return {"status": "insufficient_wallet", "deposited": 0.0, "gate_balance": gate_balance}

        to_deposit = round(shortfall, 2)
        if to_deposit > wallet_usdc:
            logger.warning(
                f"⚠️ Saldo USDC nel wallet (${wallet_usdc:.2f}) inferiore allo shortfall (${to_deposit:.2f}). "
                "Deposito l'intero saldo USDC disponibile."
            )
            to_deposit = round(wallet_usdc, 2)

        min_deposit = getattr(config, "MIN_GATE_DEPOSIT", 1.0)
        if to_deposit < min_deposit:
            logger.warning(
                f"⚠️ Importo da depositare (${to_deposit:.2f}) inferiore al minimo (${min_deposit:.2f})."
            )
            return {"status": "insufficient_wallet", "deposited": 0.0, "gate_balance": gate_balance}

        dep_res = self.deposit_usdc(to_deposit)

        # Breve attesa per permettere la sincronizzazione on-chain / SDK
        time.sleep(3)

        new_status = self.get_account_status()
        new_balance = float(new_status.get("balance_usd", 0.0))
        logger.info(
            f"🎉 Deposito Gate completato! Saldo Gate: ${gate_balance:.2f} -> ${new_balance:.2f} USDC"
        )

        return {
            "status": "deposited",
            "deposited": to_deposit,
            "previous_balance": gate_balance,
            "gate_balance": new_balance,
            "tx": dep_res.get("txHash") or dep_res.get("tx_hash"),
        }

    # ----------------------------------------------------------------------
    #                        ESECUZIONE SEGNALE AI
    # ----------------------------------------------------------------------
    def execute_signal(self, order_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Esegue l'operazione ('open', 'close', 'hold') restituita dal modello AI.
        Include auto-refuel USDC e auto-deposito nel Gate se il saldo è insufficiente.
        """
        try:
            self._validate_order_input(order_json)

            op = order_json["operation"]
            raw_symbol = order_json["symbol"]

            # 1. Operazione HOLD
            if op == "hold":
                logger.info(f"[SynFuturesTrader] HOLD — nessuna azione per {raw_symbol}.")
                return {"status": "hold", "message": f"No action taken for {raw_symbol}."}

            # Coppia inesistente su SynFutures: scarta il segnale invece di far
            # fallire il ciclo con "Instrument ... not found"
            markets = self.get_tradable_markets()
            coin = self.denormalize_symbol(str(raw_symbol).strip().upper().replace("/", "-"))
            if op == "open" and markets and coin not in markets:
                msg = (f"Nessun perpetual {coin} su SynFutures: ordine non inviato. "
                       f"Mercati disponibili: {', '.join(sorted(markets))}")
                logger.warning(f"[SynFuturesTrader] {msg}")
                return {"status": "rejected", "message": msg}
            norm_symbol = self.normalize_symbol(raw_symbol)

            # 2. Operazione CLOSE
            if op == "close":
                logger.info(f"[SynFuturesTrader] Market CLOSE per {norm_symbol} ({raw_symbol})")
                res = self._make_request("POST", "/order/close", {"symbol": norm_symbol})
                logger.info(f"✅ Posizione chiusa: {res}")
                return res

            # 3. Operazione OPEN
            direction = order_json["direction"] # 'long' o 'short'
            portion = float(order_json["target_portion_of_balance"])
            leverage = float(order_json.get("leverage", 1.0))
            stop_loss_percent = float(order_json.get("stop_loss_percent", 2.0))

            # Recupera saldo account iniziale
            account_status = self.get_account_status()
            balance_usd = account_status["balance_usd"]

            # Controllo notional minimo (SynFutures V3 su Base ha un notional minimo di circa ~$70)
            min_notional = getattr(config, "MIN_NOTIONAL_USD", 70.0)

            # Calcola margine target stimato
            target_margin_usd = balance_usd * portion if balance_usd > 0 else 25.0
            if target_margin_usd <= 0:
                target_margin_usd = 25.0

            notional = target_margin_usd * leverage
            if notional < min_notional:
                logger.info(f"⚠️ Nozionale ${notional:.2f} < minimo richiesto (${min_notional:.2f}). Aggiusto leva/margine...")
                if target_margin_usd * 10 >= min_notional:
                    leverage = min(10.0, round(min_notional / target_margin_usd, 1))
                else:
                    target_margin_usd = min_notional / leverage

            # AUTO-DEPOSITO & AUTO-REFUEL:
            # Se il saldo Gate attuale è inferiore al margine richiesto per l'ordine,
            # prova ad effettuare auto-refuel da ETH e auto-deposito sul Gate!
            if balance_usd < target_margin_usd or balance_usd <= 0:
                logger.info(
                    f"ℹ️ Saldo Gate attuale (${balance_usd:.2f}) < margine target (${target_margin_usd:.2f}). "
                    "Esecuzione auto-gestione collaterale Gate..."
                )
                try:
                    self.ensure_gate_margin(target_margin_usd)
                except Exception as dep_err:
                    logger.warning(f"⚠️ Gestione automatica collaterale non riuscita: {dep_err}")

                # Ricarica stato account aggiornato dopo eventuale deposito
                account_status = self.get_account_status()
                balance_usd = account_status["balance_usd"]

                # Ricalcola target_margin_usd in base al saldo Gate effettivo
                if balance_usd > 0:
                    target_margin_usd = min(balance_usd, max(target_margin_usd, balance_usd * portion))
                    notional = target_margin_usd * leverage
                    if notional < min_notional:
                        if target_margin_usd * 10 >= min_notional:
                            leverage = min(10.0, round(min_notional / target_margin_usd, 1))
                        else:
                            target_margin_usd = min(balance_usd, min_notional / leverage)

            if balance_usd <= 0:
                eth_info = f", ETH wallet: {self.client.eth_balance():.5f}" if self.client else ""
                usdc_info = f", USDC wallet: ${self.client.balance_of_float(config.USDC):.2f}" if self.client else ""
                msg = (
                    f"Saldo insufficiente su Gate (${balance_usd:.2f}){eth_info}{usdc_info}. "
                    f"Impossibile aprire posizione per {raw_symbol}. Ricarica ETH o USDC nel wallet."
                )
                logger.warning(f"[SynFuturesTrader] {msg}")
                return {"status": "rejected", "message": msg}

            synfutures_side = "LONG" if direction == "long" else "SHORT"

            logger.info(
                f"\n[SynFuturesTrader] Ordine Market {synfutures_side} su {norm_symbol}\n"
                f"  Margine: ${target_margin_usd:.2f} USD\n"
                f"  Leva: {leverage}x\n"
                f"  Nozionale stimato: ${target_margin_usd * leverage:.2f}\n"
                f"  Stop Loss desiderato: {stop_loss_percent}%\n"
            )

            order_payload = {
                "symbol": norm_symbol,
                "side": synfutures_side,
                "sizeUsd": round(target_margin_usd, 2),
                "leverage": leverage,
                "slippage": 100,  # 1%
            }

            res = self._make_request("POST", "/order/market", order_payload)
            logger.info(f"✅ Ordine inviato con successo: {res}")

            # Arricchisci risposta per tracciamento stop loss
            if isinstance(res, dict):
                res["stop_loss_percent"] = stop_loss_percent
                res["target_portion_of_balance"] = portion

            return res

        except Exception as exc:
            logger.error(f"[SynFuturesTrader] Errore durante esecuzione segnale: {exc}")
            return {"status": "error", "message": str(exc)}

    # ----------------------------------------------------------------------
    #                        GESTIONE LEVA & LIMITI
    # ----------------------------------------------------------------------
    def get_current_leverage(self, symbol: str) -> Dict[str, Any]:
        """Restituisce informazioni sulla leva corrente per il simbolo specificato."""
        status = self.get_account_status()
        target_coin = self.denormalize_symbol(symbol)
        for pos in status.get("open_positions", []):
            if pos["symbol"] == target_coin:
                return {
                    "coin": target_coin,
                    "leverage": pos.get("leverage", "3x"),
                    "side": pos.get("side"),
                    "size": pos.get("size")
                }
        return {"coin": target_coin, "leverage": "N/A", "note": "Nessuna posizione aperta"}

    def debug_symbol_limits(self, symbol: Optional[str] = None):
        """Visualizza i mercati disponibili su SynFutures."""
        print("\n📊 STRUMENTI DISPONIBILI SU SYNFUTURES (BASE)")
        print("-" * 60)
        try:
            instruments = self._make_request("GET", "/instruments")
            for inst in instruments:
                sym = inst.get("symbol", "")
                if symbol and self.denormalize_symbol(sym) != self.denormalize_symbol(symbol):
                    continue
                print(f"Instrument: {sym} (Addr: {inst.get('instrumentAddr', 'N/A')})")
                print(f"  Spot Price: {inst.get('spotPrice', 'N/A')}")
        except Exception as e:
            print(f"Errore recuperando strumenti: {e}")
