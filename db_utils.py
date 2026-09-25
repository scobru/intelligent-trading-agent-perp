from __future__ import annotations
import sqlite3
import json
import os
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

# Database path (default: trading_agent.db nella root del progetto)
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db")
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)


# ==============================================================================
# SCHEMA DEFINITIONS (SQLITE)
# ==============================================================================

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS account_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    balance_usd     REAL NOT NULL,
    raw_payload     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS open_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL REFERENCES account_snapshots(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    size                REAL NOT NULL,
    entry_price         REAL,
    mark_price          REAL,
    pnl_usd             REAL,
    leverage            TEXT,
    raw_payload         TEXT NOT NULL,
    stop_loss_percent   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_open_positions_snapshot_id
    ON open_positions(snapshot_id);

CREATE TABLE IF NOT EXISTS ai_contexts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    system_prompt   TEXT
);

CREATE TABLE IF NOT EXISTS indicators_contexts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id              INTEGER NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    ticker                  TEXT NOT NULL,
    ts                      TEXT,
    price                   REAL,
    ema20                   REAL,
    macd                    REAL,
    rsi_7                   REAL,
    volume_bid              REAL,
    volume_ask              REAL,
    pp                      REAL,
    s1                      REAL,
    s2                      REAL,
    r1                      REAL,
    r2                      REAL,
    open_interest_latest    REAL,
    open_interest_average   REAL,
    funding_rate            REAL,
    ema20_15m               REAL,
    ema50_15m               REAL,
    atr3_15m                REAL,
    atr14_15m               REAL,
    volume_15m_current      REAL,
    volume_15m_average      REAL,
    intraday_mid_prices     TEXT,
    intraday_ema20_series   TEXT,
    intraday_macd_series    TEXT,
    intraday_rsi7_series    TEXT,
    intraday_rsi14_series   TEXT,
    lt15m_macd_series       TEXT,
    lt15m_rsi14_series      TEXT
);

CREATE TABLE IF NOT EXISTS news_contexts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id      INTEGER NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    news_text       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_contexts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id              INTEGER NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    value                   INTEGER,
    classification          TEXT,
    sentiment_timestamp     INTEGER,
    raw                     TEXT
);

CREATE TABLE IF NOT EXISTS forecasts_contexts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id              INTEGER NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    ticker                  TEXT NOT NULL,
    timeframe               TEXT NOT NULL,
    last_price              REAL,
    prediction              REAL,
    lower_bound             REAL,
    upper_bound             REAL,
    change_pct              REAL,
    forecast_timestamp      INTEGER,
    raw                     TEXT
);

CREATE TABLE IF NOT EXISTS bot_operations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    context_id                  INTEGER REFERENCES ai_contexts(id) ON DELETE CASCADE,
    operation                   TEXT NOT NULL,
    symbol                      TEXT,
    direction                   TEXT,
    target_portion_of_balance   REAL,
    leverage                    REAL,
    raw_payload                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bot_operations_created_at
    ON bot_operations(created_at);

CREATE TABLE IF NOT EXISTS errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    error_type      TEXT NOT NULL,
    error_message   TEXT,
    traceback       TEXT,
    context         TEXT,
    source          TEXT
);

CREATE TABLE IF NOT EXISTS bot_control (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_connection():
    """Context manager per SQLite con commit/rollback automatico."""
    db_dir = os.path.dirname(os.path.abspath(SQLITE_DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Inizializza le tabelle SQLite se non già presenti."""
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


# Inizializza automaticamente lo schema se il file non esiste
init_db()


# ==============================================================================
# HELPER DI SERIALIZZAZIONE
# ==============================================================================

def _to_json_str(val: Any) -> str:
    """Converte un valore Python o dict in stringa JSON sicura."""
    if val is None:
        return "{}"
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, default=str)
    except Exception:
        return str(val)


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ==============================================================================
# FUNZIONI DI LOGGING
# ==============================================================================

def log_account_status(account_status: Dict[str, Any]) -> int:
    """Registra uno snapshot dell'account e delle sue posizioni aperte."""
    balance_usd = float(account_status.get("balance_usd", 0.0))
    raw_payload = _to_json_str(account_status)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO account_snapshots (balance_usd, raw_payload) VALUES (?, ?);",
            (balance_usd, raw_payload),
        )
        snapshot_id = cur.lastrowid

        positions = account_status.get("open_positions", [])
        if isinstance(positions, list):
            for pos in positions:
                cur.execute(
                    """
                    INSERT INTO open_positions (
                        snapshot_id, symbol, side, size, entry_price, mark_price,
                        pnl_usd, leverage, raw_payload, stop_loss_percent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        snapshot_id,
                        pos.get("symbol", "UNKNOWN"),
                        pos.get("side", "UNKNOWN"),
                        _to_float(pos.get("size")) or 0.0,
                        _to_float(pos.get("entry_price")),
                        _to_float(pos.get("mark_price")),
                        _to_float(pos.get("pnl_usd")),
                        str(pos.get("leverage", "")),
                        _to_json_str(pos),
                        pos.get("stop_loss_percent"),
                    ),
                )

        return snapshot_id


def log_bot_operation(
    order_json: Dict[str, Any],
    system_prompt: Optional[str] = None,
    indicators: Optional[Any] = None,
    news_text: Optional[str] = None,
    sentiment: Optional[Any] = None,
    forecasts: Optional[Any] = None,
) -> int:
    """Registra un'operazione decisa dal bot AI e l'eventuale contesto associato."""
    with get_connection() as conn:
        cur = conn.cursor()

        # Inserisci ai_context se disponibile
        context_id = None
        if system_prompt or indicators or news_text or sentiment or forecasts:
            cur.execute(
                "INSERT INTO ai_contexts (system_prompt) VALUES (?);",
                (system_prompt,),
            )
            context_id = cur.lastrowid

            # News
            if news_text:
                cur.execute(
                    "INSERT INTO news_contexts (context_id, news_text) VALUES (?, ?);",
                    (context_id, news_text),
                )

            # Sentiment
            if sentiment:
                if isinstance(sentiment, str):
                    try:
                        sentiment = json.loads(sentiment)
                    except Exception:
                        sentiment = {"raw": sentiment}

                val = sentiment.get("valore") or sentiment.get("value")
                classification = sentiment.get("classificazione") or sentiment.get("value_classification")
                sentiment_ts = sentiment.get("timestamp")
                cur.execute(
                    """
                    INSERT INTO sentiment_contexts (
                        context_id, value, classification, sentiment_timestamp, raw
                    ) VALUES (?, ?, ?, ?, ?);
                    """,
                    (context_id, val, classification, sentiment_ts, _to_json_str(sentiment)),
                )

            # Forecasts
            if forecasts:
                if isinstance(forecasts, str):
                    try:
                        forecasts = json.loads(forecasts)
                    except Exception:
                        forecasts = []

                if isinstance(forecasts, list):
                    for fc in forecasts:
                        cur.execute(
                            """
                            INSERT INTO forecasts_contexts (
                                context_id, ticker, timeframe, last_price, prediction,
                                lower_bound, upper_bound, change_pct, forecast_timestamp, raw
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                context_id,
                                fc.get("Ticker", ""),
                                fc.get("Timeframe", ""),
                                _to_float(fc.get("Ultimo Prezzo")),
                                _to_float(fc.get("Previsione")),
                                _to_float(fc.get("Limite Inferiore")),
                                _to_float(fc.get("Limite Superiore")),
                                _to_float(fc.get("Variazione %")),
                                fc.get("Timestamp Previsione"),
                                _to_json_str(fc),
                            ),
                        )

            # Indicators
            if indicators:
                if isinstance(indicators, str):
                    try:
                        indicators = json.loads(indicators)
                    except Exception:
                        indicators = []

                if isinstance(indicators, list):
                    for ind in indicators:
                        curr = ind.get("current", {})
                        piv = ind.get("pivot_points", {})
                        der = ind.get("derivatives", {})
                        lt = ind.get("longer_term_15m", {})
                        cur.execute(
                            """
                            INSERT INTO indicators_contexts (
                                context_id, ticker, ts, price, ema20, macd, rsi_7,
                                pp, s1, s2, r1, r2, open_interest_latest, funding_rate,
                                ema20_15m, ema50_15m, atr3_15m, atr14_15m,
                                volume_15m_current, volume_15m_average
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                context_id,
                                ind.get("ticker", ""),
                                ind.get("timestamp", ""),
                                _to_float(curr.get("price")),
                                _to_float(curr.get("ema20")),
                                _to_float(curr.get("macd")),
                                _to_float(curr.get("rsi_7")),
                                _to_float(piv.get("pp")),
                                _to_float(piv.get("s1")),
                                _to_float(piv.get("s2")),
                                _to_float(piv.get("r1")),
                                _to_float(piv.get("r2")),
                                _to_float(der.get("open_interest_latest")),
                                _to_float(der.get("funding_rate")),
                                _to_float(lt.get("ema_20_current")),
                                _to_float(lt.get("ema_50_current")),
                                _to_float(lt.get("atr_3_current")),
                                _to_float(lt.get("atr_14_current")),
                                _to_float(lt.get("volume_current")),
                                _to_float(lt.get("volume_average")),
                            ),
                        )

        # Inserisci operazione
        cur.execute(
            """
            INSERT INTO bot_operations (
                context_id, operation, symbol, direction,
                target_portion_of_balance, leverage, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                context_id,
                order_json.get("operation", "unknown"),
                order_json.get("symbol"),
                order_json.get("direction"),
                _to_float(order_json.get("target_portion_of_balance")),
                _to_float(order_json.get("leverage")),
                _to_json_str(order_json),
            ),
        )
        return cur.lastrowid


def log_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    source: Optional[str] = "trading_agent",
) -> int:
    """Registra un errore con traceback e contesto."""
    err_type = type(error).__name__
    err_msg = str(error)
    tb = traceback.format_exc()
    ctx_str = _to_json_str(context)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO errors (error_type, error_message, traceback, context, source)
            VALUES (?, ?, ?, ?, ?);
            """,
            (err_type, err_msg, tb, ctx_str, source),
        )
        return cur.lastrowid


def is_bot_paused() -> bool:
    """Verifica se il bot e' in stato di pausa (da coordinator o operatore)."""
    try:
        init_db()
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM bot_control WHERE key = 'is_paused';")
            row = cur.fetchone()
            if row:
                return str(row["value"]).lower() in ("1", "true", "yes")
    except Exception:
        pass
    return False


def get_pause_info() -> Dict[str, Any]:
    """Recupera dettagli sullo stato di pausa del bot."""
    info = {"is_paused": False, "reason": "", "updated_at": ""}
    try:
        init_db()
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value, updated_at FROM bot_control WHERE key IN ('is_paused', 'pause_reason');")
            for row in cur.fetchall():
                if row["key"] == "is_paused":
                    info["is_paused"] = str(row["value"]).lower() in ("1", "true", "yes")
                    info["updated_at"] = row["updated_at"]
                elif row["key"] == "pause_reason":
                    info["reason"] = row["value"]
    except Exception:
        pass
    return info


def set_bot_paused(paused: bool, reason: str = "") -> None:
    """Imposta o rimuove lo stato di pausa del bot."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    val = "1" if paused else "0"
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO bot_control (key, value, updated_at) VALUES ('is_paused', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """, (val, now))
        conn.execute("""
            INSERT INTO bot_control (key, value, updated_at) VALUES ('pause_reason', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """, (reason or ("Pausa da Coordinatore/Operatore" if paused else "Operativo"), now))
