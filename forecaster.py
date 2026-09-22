import pandas as pd
from datetime import datetime, timezone, timedelta
from prophet import Prophet
import ccxt
import warnings
warnings.filterwarnings('ignore')

class CryptoForecaster:
    """
    Forecasting con Prophet basato sui dati storici recuperati tramite CCXT.
    Compatibile con l'interfaccia originale di HyperliquidForecaster.
    """

    def __init__(self, testnet: bool = False):
        try:
            self.exchange = ccxt.binance({"enableRateLimit": True})
        except Exception:
            self.exchange = ccxt.kraken({"enableRateLimit": True})
        self.last_prices = {}

    def _get_symbol(self, coin: str) -> str:
        return f"{coin.upper()}/USDT"

    def _fetch_candles(self, coin: str, interval: str, limit: int) -> pd.DataFrame:
        symbol = self._get_symbol(coin)
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
        except Exception:
            fallback = ccxt.kraken({"enableRateLimit": True})
            raw = fallback.fetch_ohlcv(f"{coin.upper()}/USD", timeframe=interval, limit=limit)

        if not raw:
            raise RuntimeError(f"Nessuna candela disponibile per {coin} {interval}")

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["ds"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(None)
        df["y"] = df["close"].astype(float)

        df = df[["ds", "y"]].sort_values("ds").reset_index(drop=True)
        return df

    def forecast(self, coin: str, interval: str) -> tuple:
        if interval == "15m":
            df = self._fetch_candles(coin, "15m", limit=300)
            freq = "15min"
        else:
            df = self._fetch_candles(coin, "1h", limit=500)
            freq = "h"

        last_price = df["y"].iloc[-1]

        model = Prophet(daily_seasonality=True, weekly_seasonality=True)
        model.fit(df)

        future = model.make_future_dataframe(periods=1, freq=freq)
        forecast = model.predict(future)

        return forecast.tail(1)[["ds", "yhat", "yhat_lower", "yhat_upper"]], last_price

    def forecast_many(self, tickers: list, intervals=("15m", "1h")):
        results = []
        for coin in tickers:
            for interval in intervals:
                try:
                    forecast_data, last_price = self.forecast(coin, interval)
                    fc = forecast_data.iloc[0]
                    variazione_pct = ((fc["yhat"] - last_price) / last_price) * 100
                    timeframe = "Prossimi 15 Minuti" if interval == "15m" else "Prossima Ora"

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
                    results.append({
                        "Ticker": coin,
                        "Timeframe": "Prossimi 15 Minuti" if interval == "15m" else "Prossima Ora",
                        "Ultimo Prezzo": None,
                        "Previsione": None,
                        "Limite Inferiore": None,
                        "Limite Superiore": None,
                        "Variazione %": None,
                        "Timestamp Previsione": None,
                        "error": str(e)
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
        return df.to_string(index=False), df.to_json(orient="records")
    except Exception:
        return None, None
