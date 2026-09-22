import logging
import traceback

import pandas as pd
from prophet import Prophet
import ccxt
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)


def _first_valid_freq(*candidates: str) -> str:
    """
    Restituisce il primo alias di frequenza accettato dalla versione di pandas
    installata. pandas < 2.2 usa 'H'/'T', pandas >= 3.0 accetta solo 'h'/'min'.
    """
    for candidate in candidates:
        try:
            pd.tseries.frequencies.to_offset(candidate)
            return candidate
        except Exception:
            continue
    return candidates[0]


# Alias risolti una sola volta all'import
FREQ_15M = _first_valid_freq("15min", "15T")
FREQ_1H = _first_valid_freq("h", "H")

# Exchange provati in ordine: (id ccxt, quote currency)
EXCHANGE_CANDIDATES = (
    ("binance", "USDT"),
    ("kraken", "USD"),
    ("coinbase", "USD"),
    ("okx", "USDT"),
    ("bybit", "USDT"),
)


class ForecastError(RuntimeError):
    """Errore di previsione che conserva l'ultimo prezzo noto, se disponibile."""

    def __init__(self, message: str, last_price=None):
        super().__init__(message)
        self.last_price = last_price


class CryptoForecaster:
    """
    Forecasting con Prophet basato sui dati storici recuperati tramite CCXT.
    Compatibile con l'interfaccia originale di HyperliquidForecaster.
    """

    def __init__(self, testnet: bool = False):
        self._exchanges = {}
        self._preferred = None
        self.last_prices = {}

    def _get_symbol(self, coin: str, quote: str = "USDT") -> str:
        return f"{coin.upper()}/{quote}"

    def _get_exchange(self, exchange_id: str):
        """Istanzia (e riusa) un client ccxt per exchange_id."""
        if exchange_id not in self._exchanges:
            self._exchanges[exchange_id] = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        return self._exchanges[exchange_id]

    def _candidate_order(self):
        """L'ultimo exchange funzionante viene provato per primo."""
        candidates = list(EXCHANGE_CANDIDATES)
        if self._preferred:
            candidates.sort(key=lambda c: c[0] != self._preferred)
        return candidates

    def _fetch_candles(self, coin: str, interval: str, limit: int) -> pd.DataFrame:
        errors = []
        raw = None

        for exchange_id, quote in self._candidate_order():
            symbol = self._get_symbol(coin, quote)
            try:
                exchange = self._get_exchange(exchange_id)
                raw = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
                if raw:
                    self._preferred = exchange_id
                    break
                errors.append(f"{exchange_id}: nessuna candela per {symbol}")
                raw = None
            except Exception as exc:
                errors.append(f"{exchange_id}: {type(exc).__name__}: {exc}")
                raw = None

        if not raw:
            raise RuntimeError(
                f"Nessuna candela disponibile per {coin} {interval} — " + " | ".join(errors)
            )

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["ds"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(None)
        df["y"] = df["close"].astype(float)

        df = df[["ds", "y"]].sort_values("ds").reset_index(drop=True)
        return df

    def forecast(self, coin: str, interval: str) -> tuple:
        if interval == "15m":
            df = self._fetch_candles(coin, "15m", limit=300)
            freq = FREQ_15M
        else:
            df = self._fetch_candles(coin, "1h", limit=500)
            freq = FREQ_1H

        last_price = float(df["y"].iloc[-1])
        self.last_prices[f"{coin.upper()}:{interval}"] = last_price

        # Da qui in poi il prezzo è noto: se Prophet fallisce lo conserviamo
        # nell'eccezione, così la dashboard può comunque mostrare un valore reale.
        try:
            model = Prophet(daily_seasonality=True, weekly_seasonality=True)
            model.fit(df)

            future = model.make_future_dataframe(periods=1, freq=freq)
            forecast = model.predict(future)
        except Exception as exc:
            raise ForecastError(
                f"Prophet fallito per {coin} {interval} (freq={freq}): {type(exc).__name__}: {exc}",
                last_price=last_price,
            ) from exc

        return forecast.tail(1)[["ds", "yhat", "yhat_lower", "yhat_upper"]], last_price

    def forecast_many(self, tickers: list, intervals=("15m", "1h")):
        results = []
        for coin in tickers:
            for interval in intervals:
                timeframe = "Prossimi 15 Minuti" if interval == "15m" else "Prossima Ora"
                try:
                    forecast_data, last_price = self.forecast(coin, interval)
                    fc = forecast_data.iloc[0]
                    variazione_pct = ((fc["yhat"] - last_price) / last_price) * 100

                    results.append({
                        "Ticker": coin,
                        "Timeframe": timeframe,
                        "Ultimo Prezzo": round(last_price, 2),
                        "Previsione": round(fc["yhat"], 2),
                        "Limite Inferiore": round(fc["yhat_lower"], 2),
                        "Limite Superiore": round(fc["yhat_upper"], 2),
                        "Variazione %": round(variazione_pct, 2),
                        "Timestamp Previsione": fc["ds"]
                    })
                except Exception as e:
                    # L'errore veniva ingoiato in silenzio: senza log era impossibile
                    # capire perché una riga risultasse vuota (es. $0 in dashboard).
                    last_price = getattr(e, "last_price", None)
                    print(f"⚠️  Previsione fallita per {coin} {interval}: {type(e).__name__}: {e}")
                    logger.warning(
                        "Previsione fallita per %s %s: %s", coin, interval, e,
                        exc_info=True,
                    )
                    logger.debug(traceback.format_exc())

                    results.append({
                        "Ticker": coin,
                        "Timeframe": timeframe,
                        "Ultimo Prezzo": round(last_price, 2) if last_price is not None else None,
                        "Previsione": None,
                        "Limite Inferiore": None,
                        "Limite Superiore": None,
                        "Variazione %": None,
                        "Timestamp Previsione": None,
                        "error": f"{type(e).__name__}: {e}"
                    })
        return results

    def get_crypto_forecasts(self, tickers: list):
        self._last_results = self.forecast_many(tickers, intervals=("15m", "1h"))
        df = pd.DataFrame(self._last_results)
        if "error" in df.columns:
            df = df.drop("error", axis=1)
        return df.to_string(index=False)

# Alias retrocompatibilità
HyperliquidForecaster = CryptoForecaster

def get_hyperliquid_forecasts(tickers=["BTC", "ETH", "SOL"], testnet=False):
    forecaster = CryptoForecaster(testnet=testnet)
    return forecaster.get_crypto_forecasts(tickers)

def get_crypto_forecasts(tickers=["BTC", "ETH", "SOL"], testnet=False):
    try:
        forecaster = CryptoForecaster(testnet=testnet)
        results = forecaster.forecast_many(tickers)
        df = pd.DataFrame(results)
        # Il testo va nel prompt dell'LLM: fuori la colonna diagnostica.
        # Il JSON conserva l'errore e finisce nel DB per il debug.
        df_txt = df.drop(columns=["error"], errors="ignore")
        return df_txt.to_string(index=False), df.to_json(orient="records")
    except Exception as exc:
        print(f"❌ Previsioni Prophet non disponibili: {type(exc).__name__}: {exc}")
        logger.error("get_crypto_forecasts fallito: %s", exc, exc_info=True)
        return None, None
