"""
Paper trading: conto perpetual virtuale, prezzi veri.

Serve per vedere come si comporterebbe l'agente senza mettere capitale su
SynFutures. Gli ordini *vengono eseguiti* contro un collaterale finto: si
aprono e chiudono posizioni in leva, maturano P&L, scattano stop loss e
liquidazioni, e la dashboard mostra il conto evolvere ciclo dopo ciclo.

Quello che resta reale: prezzi di mercato (CCXT, come gli indicatori),
decisioni del modello, dimensionamento e nozionale minimo dell'ordine.
Quello che e' simulato: il collaterale sul Gate, il riempimento a mercato
(al prezzo corrente +/- lo slippage), le fee di trading. Non si simulano
funding rate ne' il prezzo mark dell'oracolo di SynFutures.

Si attiva con PAPER_TRADING=true: non servono wallet, chiave privata ne'
il microservizio synfutures-service.
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from synfutures_trader import SynFuturesTrader

load_dotenv()

logger = logging.getLogger(__name__)


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


PAPER_TRADING = _b("PAPER_TRADING", False)
PAPER_START_USDC = _f("PAPER_START_USDC", 1000.0)
# Fee taker per lato (SynFutures V3 ~ 5 bps) e slippage del riempimento simulato
PAPER_FEE_BPS = _f("PAPER_FEE_BPS", 5.0)
PAPER_SLIPPAGE_BPS = _f("PAPER_SLIPPAGE_BPS", 10.0)
# Sotto questa quota del margine iniziale la posizione viene liquidata
PAPER_MAINTENANCE_MARGIN = _f("PAPER_MAINTENANCE_MARGIN", 0.05)
MIN_NOTIONAL_USD = 70.0

# Stesso volume persistente del database, cosi' il conto sopravvive ai deploy
_DATA_DIR = os.path.dirname(os.path.abspath(
    os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db"))
))
PAPER_STATE_PATH = os.getenv("PAPER_STATE_PATH") or os.path.join(_DATA_DIR, "paper_account.json")


# ----------------------------------------------------------------------
#                               PREZZI
# ----------------------------------------------------------------------
def fetch_price(coin: str) -> Optional[float]:
    """Ultimo prezzo da Binance (fallback Kraken), come in indicators.py."""
    import ccxt

    for exchange_id, quote in (("binance", "USDT"), ("kraken", "USD")):
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            ticker = exchange.fetch_ticker(f"{coin.upper()}/{quote}")
            price = ticker.get("last") or ticker.get("close")
            if price:
                return float(price)
        except Exception as exc:  # rete, simbolo assente, rate limit
            logger.warning("Prezzo %s da %s non disponibile: %s", coin, exchange_id, exc)
    return None


# ----------------------------------------------------------------------
#                            CONTO VIRTUALE
# ----------------------------------------------------------------------
class PaperPerpAccount:
    """Collaterale e posizioni virtuali, persistiti su file JSON."""

    def __init__(self, path: str = None, start_usdc: float = None):
        self.path = path or PAPER_STATE_PATH
        self.start_usdc = PAPER_START_USDC if start_usdc is None else start_usdc
        self.state: Dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------ persistenza
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except (OSError, ValueError):
            self.state = {}
        if not self.state:
            self.state = {
                "usdc": float(self.start_usdc),
                "initial_usdc": float(self.start_usdc),
                "positions": {},       # coin -> {side, size, entry_price, margin, leverage, ...}
                "created_at": time.time(),
                "fees_paid_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "trades": 0,
                "stop_losses": 0,
                "liquidations": 0,
            }
            self.save()
            logger.info("Conto paper inizializzato con $%.2f USDC", self.usdc)

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2)
        except OSError as exc:
            logger.warning("Impossibile salvare %s: %s", self.path, exc)

    # ------------------------------------------------------------ saldi
    @property
    def usdc(self) -> float:
        return float(self.state.get("usdc", 0.0))

    @property
    def positions(self) -> Dict[str, Dict[str, Any]]:
        return self.state.setdefault("positions", {})

    @staticmethod
    def unrealized(pos: Dict[str, Any], price: float) -> float:
        sign = 1 if pos["side"] == "long" else -1
        return (price - float(pos["entry_price"])) * float(pos["size"]) * sign

    def equity(self, prices: Dict[str, float]) -> float:
        total = self.usdc
        for coin, pos in self.positions.items():
            price = prices.get(coin) or float(pos.get("last_price") or pos["entry_price"])
            total += float(pos["margin"]) + self.unrealized(pos, price)
        return total

    def _fee(self, notional: float) -> float:
        fee = notional * PAPER_FEE_BPS / 10_000
        self.state["fees_paid_usd"] = float(self.state.get("fees_paid_usd", 0.0)) + fee
        return fee

    # ------------------------------------------------------------ operazioni
    def open(self, coin: str, side: str, margin_usd: float, leverage: float, price: float,
             stop_loss_percent: float = None) -> Dict[str, Any]:
        coin = coin.upper()
        existing = self.positions.get(coin)
        if existing and existing["side"] != side:
            # stesso comportamento di un conto netto: il lato opposto chiude prima
            self.close(coin, price, reason="flip")

        margin_usd = min(margin_usd, self.usdc)
        if margin_usd <= 0:
            raise ValueError(f"Collaterale virtuale insufficiente (${self.usdc:.2f})")

        slip = PAPER_SLIPPAGE_BPS / 10_000
        fill = price * (1 + slip if side == "long" else 1 - slip)
        notional = margin_usd * leverage
        fee = self._fee(notional)
        size = (notional - fee) / fill

        self.state["usdc"] = self.usdc - margin_usd
        pos = self.positions.get(coin)
        if pos:
            # incremento: prezzo medio ponderato, margine e taglia sommati
            new_size = float(pos["size"]) + size
            pos["entry_price"] = (float(pos["entry_price"]) * float(pos["size"]) + fill * size) / new_size
            pos["size"] = new_size
            pos["margin"] = float(pos["margin"]) + margin_usd
        else:
            pos = self.positions[coin] = {
                "side": side,
                "size": size,
                "entry_price": fill,
                "margin": margin_usd,
                "opened_at": time.time(),
            }
        pos["leverage"] = round(float(pos["size"]) * fill / float(pos["margin"]), 2)
        pos["last_price"] = price
        if stop_loss_percent:
            pos["stop_loss_percent"] = float(stop_loss_percent)
        self.state["trades"] = int(self.state.get("trades", 0)) + 1
        self.save()
        return {
            "symbol": coin, "side": side, "fill_price": round(fill, 4), "size": size,
            "margin_usd": round(margin_usd, 2), "notional_usd": round(notional, 2),
            "fee_usd": round(fee, 4), "usdc_after": round(self.usdc, 2),
        }

    def close(self, coin: str, price: float, reason: str = "signal") -> Dict[str, Any]:
        coin = coin.upper()
        pos = self.positions.get(coin)
        if not pos:
            raise ValueError(f"Nessuna posizione virtuale aperta su {coin}")

        slip = PAPER_SLIPPAGE_BPS / 10_000
        fill = price * (1 - slip if pos["side"] == "long" else 1 + slip)
        pnl = self.unrealized(pos, fill)
        fee = self._fee(float(pos["size"]) * fill)
        returned = max(0.0, float(pos["margin"]) + pnl - fee)

        self.state["usdc"] = self.usdc + returned
        self.state["realized_pnl_usd"] = float(self.state.get("realized_pnl_usd", 0.0)) + returned - float(pos["margin"])
        self.state["trades"] = int(self.state.get("trades", 0)) + 1
        if reason == "stop_loss":
            self.state["stop_losses"] = int(self.state.get("stop_losses", 0)) + 1
        elif reason == "liquidation":
            self.state["liquidations"] = int(self.state.get("liquidations", 0)) + 1
        del self.positions[coin]
        self.save()
        return {
            "symbol": coin, "side": pos["side"], "reason": reason,
            "fill_price": round(fill, 4), "pnl_usd": round(pnl - fee, 4),
            "usdc_after": round(self.usdc, 2),
        }

    def mark(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Aggiorna i prezzi e fa scattare stop loss e liquidazioni.
        Il controllo avviene a ogni ciclo, quindi sul prezzo corrente e non
        sul minimo/massimo intermedio: un limite dichiarato del simulatore.
        """
        events = []
        for coin in list(self.positions):
            pos = self.positions[coin]
            price = prices.get(coin)
            if not price:
                continue
            pos["last_price"] = price
            entry = float(pos["entry_price"])
            move_pct = (price - entry) / entry * 100 * (1 if pos["side"] == "long" else -1)
            pnl = self.unrealized(pos, price)
            sl = pos.get("stop_loss_percent")

            if float(pos["margin"]) + pnl <= float(pos["margin"]) * PAPER_MAINTENANCE_MARGIN:
                events.append(self.close(coin, price, reason="liquidation"))
            elif sl and move_pct <= -abs(float(sl)):
                events.append(self.close(coin, price, reason="stop_loss"))
        self.save()
        return events

    def summary(self, prices: Dict[str, float] = None) -> Dict[str, Any]:
        equity = self.equity(prices or {})
        initial = float(self.state.get("initial_usdc", self.start_usdc))
        return {
            "initial_usdc": initial,
            "equity_usd": round(equity, 2),
            "pnl_usd": round(equity - initial, 2),
            "realized_pnl_usd": round(float(self.state.get("realized_pnl_usd", 0.0)), 2),
            "fees_paid_usd": round(float(self.state.get("fees_paid_usd", 0.0)), 4),
            "trades": int(self.state.get("trades", 0)),
            "stop_losses": int(self.state.get("stop_losses", 0)),
            "liquidations": int(self.state.get("liquidations", 0)),
            "created_at": self.state.get("created_at"),
        }


# ----------------------------------------------------------------------
#                    TRADER PAPER (stessa interfaccia)
# ----------------------------------------------------------------------
class PaperSynFuturesTrader(SynFuturesTrader):
    """
    Drop-in di SynFuturesTrader: stessi metodi e stesso schema di risposta,
    ma esegue sul conto virtuale. Nessuna chiamata al microservizio.
    """

    def __init__(self, account: PaperPerpAccount = None,
                 price_fn: Callable[[str], Optional[float]] = None):
        # niente super().__init__: non serve (ne' esiste) il servizio Node
        self.account = account or PaperPerpAccount()
        self.price_fn = price_fn or fetch_price
        self.account_address = "paper"
        self._markets: Dict[str, str] = {}
        self.last_events: List[Dict[str, Any]] = []

    def get_tradable_markets(self) -> Dict[str, str]:
        return {}  # in paper i prezzi arrivano da Binance/Kraken: nessun filtro

    def filter_tradable(self, tickers: List[str]) -> List[str]:
        return list(tickers)

    def _prices(self, coins) -> Dict[str, float]:
        prices = {}
        for coin in coins:
            price = self.price_fn(coin)
            if price:
                prices[coin] = price
        return prices

    def get_account_status(self) -> Dict[str, Any]:
        prices = self._prices(list(self.account.positions))
        self.last_events = self.account.mark(prices)
        for ev in self.last_events:
            logger.warning("[paper] %s %s chiusa a $%.4f (P&L $%+.2f)",
                           ev["reason"].upper(), ev["symbol"], ev["fill_price"], ev["pnl_usd"])

        open_positions = []
        for coin, pos in self.account.positions.items():
            price = prices.get(coin) or float(pos.get("last_price") or pos["entry_price"])
            open_positions.append({
                "symbol": coin,
                "side": pos["side"],
                "size": round(float(pos["size"]), 6),
                "entry_price": round(float(pos["entry_price"]), 4),
                "mark_price": round(price, 4),
                "pnl_usd": round(self.account.unrealized(pos, price), 4),
                "leverage": f"{pos.get('leverage', 1)}x",
                "margin_usd": round(float(pos["margin"]), 2),
                "stop_loss_percent": pos.get("stop_loss_percent"),
            })

        summary = self.account.summary(prices)
        return {
            "balance_usd": round(self.account.usdc, 2),
            "total_value_usd": summary["equity_usd"],
            "open_positions": open_positions,
            "mode": "paper",
            "paper_trading": True,
            "paper": summary,
            "paper_events": self.last_events,
            "pnl_since_start_usd": summary["pnl_usd"],
        }

    def execute_signal(self, order_json: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_order_input(order_json)
        op = order_json["operation"]
        coin = self.denormalize_symbol(self.normalize_symbol(order_json["symbol"]))

        if op == "hold":
            return {"status": "hold", "message": f"No action taken for {coin}."}

        price = self.price_fn(coin)
        if not price:
            return {"status": "error", "message": f"Prezzo di {coin} non disponibile: ordine paper non eseguito."}

        if op == "close":
            if coin not in self.account.positions:
                return {"status": "rejected", "message": f"Nessuna posizione paper aperta su {coin}."}
            return dict(self.account.close(coin, price), status="paper")

        # OPEN: stessa logica di dimensionamento del trader reale
        portion = float(order_json["target_portion_of_balance"])
        leverage = float(order_json.get("leverage", 1.0))
        stop_loss_percent = float(order_json.get("stop_loss_percent", 2.0))
        balance_usd = self.account.usdc
        if balance_usd <= 0:
            return {"status": "rejected", "message": "Collaterale virtuale esaurito."}

        margin = balance_usd * portion
        if margin <= 0:
            margin = min(balance_usd, 25.0)
        if margin * leverage < MIN_NOTIONAL_USD:
            if margin * 10 >= MIN_NOTIONAL_USD:
                leverage = min(10.0, round(MIN_NOTIONAL_USD / margin, 1))
            else:
                margin = min(balance_usd, MIN_NOTIONAL_USD / leverage)

        res = self.account.open(coin, order_json["direction"], margin, leverage, price, stop_loss_percent)
        res.update(status="paper", stop_loss_percent=stop_loss_percent,
                   target_portion_of_balance=portion, leverage=leverage)
        return res
