import os
import json
import logging
import time
import requests
from typing import Dict, Any, List, Optional
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("SynFuturesTrader")

class SynFuturesTrader:
    """
    Exchange Trader per SynFutures V3 (Base Chain).
    Fornisce un'interfaccia compatibile con HyperLiquidTrader per
    la gestione delle posizioni, balance ed esecuzione dei segnali AI.
    Comunica tramite REST con il microservizio synfutures-service (Node.js/Oyster SDK).
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

        # Verifica preliminare raggiungibilità del servizio
        self._check_service_health()

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
            "direction",
            "target_portion_of_balance",
            "leverage",
            "reason",
        ]
        for f in required_fields:
            if f not in order_json:
                raise ValueError(f"Missing required field: {f}")

        if not isinstance(order_json["symbol"], str) or not order_json["symbol"].strip():
            raise ValueError("symbol must be a non-empty string")

        if order_json["operation"] not in ("open", "close", "hold"):
            raise ValueError("operation must be 'open', 'close', or 'hold'")

        if order_json["direction"] not in ("long", "short"):
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
        }

    # ----------------------------------------------------------------------
    #                        ESECUZIONE SEGNALE AI
    # ----------------------------------------------------------------------
    def execute_signal(self, order_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Esegue l'operazione ('open', 'close', 'hold') restituita dal modello AI.
        """
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

        # Recupera saldo account
        account_status = self.get_account_status()
        balance_usd = account_status["balance_usd"]

        if balance_usd <= 0:
            raise RuntimeError(f"Saldo insufficiente su Gate ($0.00). Impossibile aprire posizione per {raw_symbol}.")

        # Calcola margine (collaterale in USD)
        target_margin_usd = balance_usd * portion
        if target_margin_usd <= 0:
            target_margin_usd = min(balance_usd, 25.0)

        # Controllo notional minimo (SynFutures V3 su Base ha un notional minimo di circa ~$70)
        min_notional = 70.0
        notional = target_margin_usd * leverage
        if notional < min_notional:
            logger.info(f"⚠️ Nozionale ${notional:.2f} < minimo richiesto (${min_notional:.2f}). Aggiusto leva/margine...")
            if target_margin_usd * 10 >= min_notional:
                leverage = min(10.0, round(min_notional / target_margin_usd, 1))
            else:
                target_margin_usd = min(balance_usd, min_notional / leverage)

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
