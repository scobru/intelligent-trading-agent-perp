<img src="static/icon.svg" alt="" width="88" height="88" align="left">

# Intelligent Trading Agent (SynFutures & OpenRouter)

<br clear="left">

**English** · [Italiano](README.it.md)

> ⚠️ **Experimental software, not financial advice.** The bot trades real money on Base and can lose some or all of the capital you give it. Start with paper trading or dry-run; when you go live, use a dedicated wallet and only amounts you can afford to lose. See the **Disclaimer** section at the bottom.

![Trading Agent](/img.jpg)

**Intelligent Trading Agent** is a quantitative, AI-driven trading agent based
on the structure of [rizzo-trading-agent](https://github.com/Rizzo-AI-Academy/rizzo-trading-agent),
adapted to trade on the decentralized perpetual DEX **SynFutures V3** (on
**Base**) and powered by LLMs available through **OpenRouter**
(`openrouter/free`).

The agent analyzes intraday market data (15m), technical indicators,
sentiment, news and time-series forecasts to build and automatically place
leveraged orders on major crypto assets (`BTC`, `ETH`).

It is part of the [Intelligent Trading](https://github.com/scobru/intelligent-trading)
suite of agents for Base.

---

## 🌟 Main features

- 🔄 **SynFutures V3 integration (Base chain)**: perpetual order execution
  (LONG/SHORT), position closing and USDC balance checks through the dedicated
  Node.js microservice (`synfutures-service`) built on the official
  `@synfutures/oyster-sdk`.
- 🧠 **OpenRouter decision engine (`openrouter/free`)**: trading signals
  generated as structured, validated JSON, keeping API costs down with
  OpenRouter's free router.
- 📊 **Intraday technical analysis (15m)**: multi-timeframe indicators
  computed with `ta` and `ccxt` (EMA 20/50, MACD, RSI 7/14, ATR 3/14, daily
  pivot points and order book volume).
- 🔮 **Machine learning forecasting**: predictive models based on Meta's
  `Prophet` to estimate the price at 15 minutes and 1 hour.
- 🎭 **Sentiment & news feed**: the 100% free *Fear & Greed* index from
  **Alternative.me** (no API key needed) and real-time parsing of the latest
  news from the *CoinJournal RSS* feed.
- 🐋 **Whale alerts**: monitoring of large capital flows and transactions.
- 🗄️ **Database & logging**: every operation, signal, portfolio snapshot and
  error is tracked in a lightweight local **SQLite** database
  (`trading_agent.db`), no external server needed.

---

## 📁 Project structure

```
intelligent-trading-agent/
├── synfutures-service/       # Node.js microservice (SynFutures Oyster SDK on Base)
│   ├── src/
│   │   ├── index.ts          # Express REST API server (port 3100)
│   │   └── synfutures.ts     # Oyster SDK wrapper for on-chain transactions
│   ├── package.json
│   └── tsconfig.json
├── main.py                   # Main script: data pipeline -> OpenRouter -> SynFutures execution
├── synfutures_trader.py      # SynFutures trading adapter (replaces HyperLiquidTrader)
├── paper.py                  # Virtual perpetual account for paper trading
├── dashboard.py              # Web dashboard (styles in static/dashboard.css|js)
├── dashboard_auth.py         # Token check for the dashboard commands
├── trading_agent.py          # LLM decision module with OpenRouter (openrouter/free)
├── indicators.py             # 15m crypto technical analysis with CCXT and TA
├── forecaster.py             # Price forecasts with Prophet
├── sentiment.py              # Fear & Greed Index
├── news_feed.py              # Crypto news RSS parsing
├── whalealert.py             # Whale transaction monitoring
├── utils.py                  # Stop loss checks and state deltas
├── db_utils.py               # Persistent SQLite logger (trading_agent.db)
├── test_trading.py           # Test script for orders and account checks
├── system_prompt.txt         # System prompt for the LLM
├── formatted_system_prompt.txt # Example of an assembled prompt
├── account_status_old.json   # Position history cache
├── requirements.txt          # Python dependencies
└── .env.example              # Environment variables template
```

---

## 🚀 Installation and usage

### 1. Requirements
- Python 3.10+
- Node.js 18+ (with npm, pnpm or yarn)

### 2. Python environment
Install the Python dependencies:
```bash
pip install -r requirements.txt
```

### 3. SynFutures microservice
The microservice handles the cryptographic signing of EVM transactions and the
direct interaction with the Oyster contracts on Base:
```bash
cd synfutures-service
npm install
npm run build
npm start
```
The service starts on `http://localhost:3100` by default.

### 4. Environment variables
Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```
Required variables:
- `OPENROUTER_API_KEY`: your API key from [openrouter.ai](https://openrouter.ai/)
- `SYNFUTURES_WALLET`: your wallet address (Base chain)
- `SYNFUTURES_PRIVATE_KEY`: private key used to sign transactions
- `SYNFUTURES_SERVICE_URL`: `http://localhost:3100` (default)
- `SQLITE_DB_PATH`: local SQLite database path (optional, default: `trading_agent.db`)
- `CMC_PRO_API_KEY`: optional (the bot uses the 100% free Alternative.me API by default)

The `.env.example` starts with `PAPER_TRADING="true"`: set it to `false` only
when you are ready to trade live.

### 5. Check and test
To test the connection to the SynFutures service and check the trader's
response:
```bash
python test_trading.py
```

### 6. Local run
Start the agent's full cycle:
```bash
python main.py
```

---

## 🚢 Deploying on CapRover & Docker

The project is preconfigured for instant deployment on **CapRover** or any
**Docker** environment:

### Included configuration files
- **`captain-definition`**: standard CapRover file (`schemaVersion: 2` pointing to the `Dockerfile`).
- **`Dockerfile`**: unified multi-runtime image with Python 3.11 + Node.js 20.
- **`start.sh`**: starts `synfutures-service` in the background, waits until it
  is ready, and runs `main.py` at regular intervals (default: every 900
  seconds / 15 minutes).
- **`docker-compose.yml`**: for quick local runs or tests.

### Deploying on CapRover
1. In the CapRover dashboard, create a new app (e.g. `intelligent-trading-agent`).
2. In the **App Configs** tab:
   - Add your environment variables:
     - `OPENROUTER_API_KEY`
     - `SYNFUTURES_WALLET`
     - `SYNFUTURES_PRIVATE_KEY`
     - `SYNFUTURES_SERVICE_URL=http://localhost:3100`
     - `INTERVAL_SECONDS=900` (trading interval, 15 minutes)
     - `SQLITE_DB_PATH=/app/data/trading_agent.db`
   - Configure a persistent volume to keep the database:
     - **Path in Container**: `/app/data`
     - **Label**: `trading-agent-data`
3. In the **Deployment** tab:
   - **GitHub method**: enter the repository
     `https://github.com/scobru/intelligent-trading-agent-perp` and the `main` branch.
   - Or with the CapRover CLI: `caprover deploy` from your terminal.
4. CapRover builds the image and starts the container automatically.

### Local run with Docker Compose
```bash
docker compose up -d --build
```
Follow the bot's logs:
```bash
docker compose logs -f
```

---

## 🛡️ Risk management
- Every operation includes a dynamic margin calculation based on the share of
  capital allocated (`target_portion_of_balance`).
- Automatic check of the minimum notional required on Base (~$70) before
  sending to SynFutures.
- Existing pairs only: every cycle the tickers in `TRADING_TICKERS` (default
  `BTC,ETH`) are matched against the SynFutures perpetual instruments
  (`/instruments`); missing ones are excluded from analysis, prompt and orders,
  and a signal on an unlisted pair becomes `hold`/`rejected` instead of an
  error.
- Local stop loss tracking through `utils.py` and `account_status_old.json`.

---

## 📝 Paper trading

With `PAPER_TRADING=true` the agent trades on a **virtual perpetual account**
(`PAPER_START_USDC`, default $1000) with **real market prices**: orders are
actually executed against the fake collateral, so you can watch P&L, stop
losses and liquidations work without capital on SynFutures.

| Real | Simulated |
|-------|----------|
| prices (Binance, Kraken fallback), model decisions, sizing and minimum notional | Gate collateral, fills (± `PAPER_SLIPPAGE_BPS`), fees (`PAPER_FEE_BPS`), stop losses and liquidations checked every cycle |

In paper mode you need no wallet, private key or `synfutures-service`
microservice (`start.sh` does not start it). SynFutures funding rates and
oracle price are not simulated. The state lives in `paper_account.json` next to
the database (on the persistent volume): delete it to start from scratch.

---

## 🖥️ Dashboard

At `http://localhost:3000`. The "Run cycle now" button stays disabled until you
set `DASHBOARD_RUN_TOKEN`: the dashboard has no login and a cycle can sign
transactions. The browser asks for the token once and remembers it.

### A consistent dashboard across the suite

All the agents in the suite share the same design system:
`static/dashboard.css` and `static/dashboard.js` are **identical in every
repository** (if you change them, copy them to the others). Every page has the
same structure: header with a mode badge (`LIVE` / `PAPER` / `DRY-RUN`), paper
trading panel, KPIs, equity curve, positions and last AI decision, bot-specific
sections, operation history and errors. Only the accent color and the icon
change.

#### Wallet and gas

Below the header, outside paper trading, the dashboard shows the bot's wallet:
ETH for gas (with its dollar value), free USDC, address with a Basescan link
and a status: **OK**, **RUNNING LOW** (below `GAS_WARN_ETH`) or **TOP UP NOW**
(below the minimum reserve). The same warning appears in the cycle's Telegram
report.

---

## 🎨 Project icon

The assets are in `static/`:

| File | Use |
|------|-----|
| `icon.svg` | main icon (vector), logo in the dashboard and README |
| `icon-small.svg` | simplified variant, source for the small sizes |
| `favicon.ico` | multi-resolution favicon (16, 32, 48 px) |
| `icon-192.png`, `icon-512.png` | PWA and sharing |
| `apple-touch-icon.png` | iOS home screen |
| `site.webmanifest` | PWA manifest |
| `dashboard.css`, `dashboard.js` | design system shared with the sibling bots |

The sources are the SVGs; the rasters are regenerated with
`python tools/generate_icons.py` (requires `pip install cairosvg pillow`,
development-only dependencies).

---

## ⚠️ Disclaimer

This software is experimental and provided "as is", without warranty of any
kind (see the MIT license). It is not financial advice nor an invitation to
invest.

- **You can lose money.** Bugs, wrong model decisions, slippage, protocol
  exploits, manipulated oracles and liquidations can cause the loss of some or
  all of your capital.
- **Decisions are made by an LLM.** It can be wrong or behave unpredictably:
  the executor's limits reduce the damage, they do not eliminate it. Past
  results, paper ones included, do not guarantee future ones.
- **Start with paper or dry-run.** When live, use a wallet dedicated to the
  bot, with amounts you can afford to lose, and never reuse that private key
  elsewhere.
- **Protect your keys.** The private key belongs only in the deployment's
  environment variables: never commit it. Without `DASHBOARD_RUN_TOKEN` the
  dashboard commands stay disabled: set it to a long random value before
  exposing the dashboard to the Internet.
- **Laws and taxes.** You are responsible for complying with the rules and tax
  obligations of your country.
- **Perp orders.** Here `DRY_RUN` only blocks on-chain swaps (refuel, Gate
  deposit): orders on SynFutures go out as soon as the microservice has the
  key. To try it out use `PAPER_TRADING=true` (the `.env.example` starts that
  way). With leverage, losses can quickly exceed the margin.

## 📜 License
Released under the MIT license. Inspired by Alpha Arena and Rizzo AI Academy.
