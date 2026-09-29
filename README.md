<img src="static/icon.svg" alt="" width="88" height="88" align="left">

# Intelligent Trading Agent (SynFutures & OpenRouter)

<br clear="left">

> ⚠️ **Software sperimentale, non consulenza finanziaria.** Il bot opera con denaro reale su Base e può perdere in parte o del tutto il capitale che gli affidi. Parti in paper trading o dry-run; in live usa un wallet dedicato e solo importi che puoi permetterti di perdere. Dettagli nella sezione **Avvertenza** in fondo.

![Trading Agent](/img.jpg)

**Intelligent Trading Agent** è un agente di trading quantitativo e AI-driven basato sulla struttura di [rizzo-trading-agent](https://github.com/Rizzo-AI-Academy/rizzo-trading-agent), specificamente adattato per operare sul DEX perpetuo decentralizzato **SynFutures V3** (su rete **Base**) e potenziato tramite i modelli LLM accessibili via **OpenRouter** (`openrouter/free`).

L’agente analizza dati di mercato intraday (15m), indicatori tecnici, sentiment, notizie e previsioni di serie temporali per formulare e piazzare automaticamente ordini di trading in leva su asset crypto principali (`BTC`, `ETH`).

---

## 🌟 Caratteristiche Principali

- 🔄 **Integrazione SynFutures V3 (Base Chain)**: Esecuzione ordini perpetual (LONG/SHORT), chiusura posizioni e consultazione balance USDC tramite il microservizio Node.js dedicato (`synfutures-service`) basato sull'SDK ufficiale `@synfutures/oyster-sdk`.
- 🧠 **Decision Engine OpenRouter (`openrouter/free`)**: Generazione automatica dei segnali di trading in formato JSON strutturato, validato e compatibile, minimizzando i costi delle API tramite il router gratuito di OpenRouter.
- 📊 **Analisi Tecnica Intraday (15m)**: Indicatori multi-timeframe calcolati con `ta` e `ccxt` (EMA 20/50, MACD, RSI 7/14, ATR 3/14, Pivot Points Giornalieri e Orderbook Volume).
- 🔮 **Machine Learning Forecasting**: Modelli predittivi basati su `Prophet` di Meta per stimare l'andamento dei prezzi a 15 minuti e ad 1 ora.
- 🎭 **Sentiment & News Feed**: Integrazione dell'indice *Fear & Greed* 100% gratuito da **Alternative.me** (senza API key richiesta) e parsing in tempo reale delle ultime notizie da *CoinJournal RSS*.
- 🐋 **Whale Alerts**: Monitoraggio dei flussi e transazioni di grandi capitali.
- 🗄️ **Database & Logging**: Tracciamento di ogni operazione, segnale, snapshot di portafoglio ed errore su database locale leggero **SQLite** (`trading_agent.db`) senza necessità di server esterni.

---

## 📁 Struttura del Progetto

```
intelligent-trading-agent/
├── synfutures-service/       # Microservizio Node.js (Oyster SDK SynFutures su Base)
│   ├── src/
│   │   ├── index.ts          # Server REST API Express (porta 3100)
│   │   └── synfutures.ts     # Wrapper Oyster SDK per transazioni on-chain
│   ├── package.json
│   └── tsconfig.json
├── main.py                   # Script principale: pipeline dati -> OpenRouter -> esecuzione SynFutures
├── synfutures_trader.py      # Adapter di trading per SynFutures (sostituto di HyperLiquidTrader)
├── paper.py                  # Conto perpetual virtuale per il paper trading
├── dashboard.py              # Dashboard web (stile in static/dashboard.css|js)
├── trading_agent.py          # Modulo decisionale LLM con OpenRouter (openrouter/free)
├── indicators.py             # Analisi tecnica crypto a 15m con CCXT e TA
├── forecaster.py             # Previsioni di prezzo con Prophet
├── sentiment.py              # Recupero Fear & Greed Index
├── news_feed.py              # Parsing feed RSS notizie crypto
├── whalealert.py             # Monitoraggio transazioni balene
├── utils.py                  # Controllo Stop Loss e deltas di stato
├── db_utils.py               # Logger persistente SQLite (trading_agent.db)
├── test_trading.py           # Script di test per ordini e verifica account
├── system_prompt.txt         # Prompt di sistema per l'LLM
├── formatted_system_prompt.txt # Esempio prompt assemblato
├── account_status_old.json   # Cache storico posizioni
├── requirements.txt          # Dipendenze Python
└── .env.example              # Template variabili d'ambiente
```

---

## 🚀 Guida all'Installazione e Utilizzo

### 1. Prerequisiti
- Python 3.10+
- Node.js 18+ (con npm o pnpm o yarn)

### 2. Configurazione Ambiente Python
Installa le dipendenze Python:
```bash
pip install -r requirements.txt
```

### 3. Configurazione del Microservizio SynFutures
Il microservizio gestisce la firma crittografica delle transazioni EVM e l'interazione diretta con i contratti Oyster su Base:
```bash
cd synfutures-service
npm install
npm run build
npm start
```
Il servizio partirà di default su `http://localhost:3100`.

### 4. Configurazione Variabili d'Ambiente
Copia il file `.env.example` in `.env` e inserisci le tue chiavi:
```bash
cp .env.example .env
```
Variabili richieste:
- `OPENROUTER_API_KEY`: La tua chiave API da [openrouter.ai](https://openrouter.ai/)
- `SYNFUTURES_WALLET`: Indirizzo del tuo wallet (Base chain)
- `SYNFUTURES_PRIVATE_KEY`: Chiave privata per firmare le transazioni
- `SYNFUTURES_SERVICE_URL`: `http://localhost:3100` (default)
- `SQLITE_DB_PATH`: Percorso database SQLite locale (opzionale, default: `trading_agent.db`)
- `CMC_PRO_API_KEY`: Opzionale (il bot usa già di default l'API 100% gratuita di Alternative.me)

### 5. Verifica e Test
Per testare la connessione al servizio SynFutures e verificare la risposta del trader:
```bash
python test_trading.py
```

### 6. Esecuzione Live (Locale)
Avvia il ciclo completo dell'agente:
```bash
python main.py
```

---

## 🚢 Distribuzione su CapRover & Docker

Il progetto è preconfigurato per il deployment istantaneo su **CapRover** o qualsiasi ambiente **Docker**:

### File di configurazione inclusi:
- **`captain-definition`**: File standard per CapRover (`schemaVersion: 2` che punta a `Dockerfile`).
- **`Dockerfile`**: Immagine unificata multi-ambiente con Python 3.11 + Node.js 20.
- **`start.sh`**: Avvia automaticamente `synfutures-service` in background, attende che sia pronto, e fa girare `main.py` ad intervalli regolari (default: ogni 900 secondi / 15 minuti).
- **`docker-compose.yml`**: Per esecuzione o test rapido in locale.

### Deployment su CapRover:
1. Nella dashboard di CapRover, crea una nuova App (es. `intelligent-trading-agent`).
2. Nella scheda **App Configs**:
   - Inserisci le tue variabili d'ambiente (Environment Variables):
     - `OPENROUTER_API_KEY`
     - `SYNFUTURES_WALLET`
     - `SYNFUTURES_PRIVATE_KEY`
     - `SYNFUTURES_SERVICE_URL=http://localhost:3100`
     - `INTERVAL_SECONDS=900` (intervallo di trading, 15 minuti)
     - `SQLITE_DB_PATH=/app/data/trading_agent.db`
   - Configura un volume persistente per salvare il database:
     - **Path in Container**: `/app/data`
     - **Label**: `trading-agent-data`
3. Nella scheda **Deployment**:
   - **Metodo GitHub**: Inserisci il repository `https://github.com/scobru/intelligent-trading-agent` e il branch `main`.
   - Oppure tramite CapRover CLI: `caprover deploy` dal tuo terminale.
4. CapRover costruirà automaticamente l'immagine e avvierà il container!

### Avvio locale con Docker Compose:
```bash
docker compose up -d --build
```
Visualizza i log del bot:
```bash
docker compose logs -f
```

---

## 🛡️ Gestione del Rischio
- Ogni operazione include un calcolo dinamico del margine in base alla frazione di capitale allocata (`target_portion_of_balance`).
- Verifica automatica del notional minimo richiesto su Base (~$70) prima dell'invio a SynFutures.
- Solo coppie esistenti: a ogni ciclo i ticker di `TRADING_TICKERS` (default `BTC,ETH`) vengono confrontati con gli strumenti perpetual di SynFutures (`/instruments`); quelli assenti sono esclusi da analisi, prompt e ordini, e un segnale su una coppia non quotata diventa `hold`/`rejected` invece di un errore.
- Tracciamento locale degli Stop Loss tramite `utils.py` e `account_status_old.json`.

---

## 📝 Paper trading

Con `PAPER_TRADING=true` l'agente opera su un **conto perpetual virtuale**
(`PAPER_START_USDC`, default $1000) con **prezzi di mercato reali**: gli ordini
vengono eseguiti davvero contro il collaterale finto, quindi si vedono P&L,
stop loss e liquidazioni lavorare senza capitale su SynFutures.

| Reale | Simulato |
|-------|----------|
| prezzi (Binance, fallback Kraken), decisioni del modello, dimensionamento e nozionale minimo | collaterale sul Gate, riempimento (± `PAPER_SLIPPAGE_BPS`), fee (`PAPER_FEE_BPS`), stop loss e liquidazioni controllati a ogni ciclo |

In paper non servono wallet, chiave privata né il microservizio
`synfutures-service` (`start.sh` non lo avvia). Funding rate e prezzo
dell'oracolo SynFutures non sono simulati. Lo stato vive in
`paper_account.json` accanto al database (sul volume persistente): per
ricominciare da zero cancellalo.

---

## 🖥️ Dashboard

Su `http://localhost:3000`. Il pulsante "Esegui ciclo ora" è disattivato
finché non imposti `DASHBOARD_RUN_TOKEN`: la dashboard non ha login e un ciclo
può firmare transazioni. Il browser chiede il token una volta e lo ricorda.

### Dashboard coerente fra i tre agenti

Le dashboard di `intelligent-trading-agent`, `-degen` e `-yield` condividono lo
stesso design system: `static/dashboard.css` e `static/dashboard.js` sono
**identici nei tre repository** (se li modifichi, copiali negli altri due).
Ogni pagina ha la stessa struttura: header con badge di modalità
(`LIVE` / `PAPER` / `DRY-RUN`), pannello paper trading, KPI, andamento del
capitale, posizioni e ultima decisione AI, sezioni specifiche del bot, storico
operazioni ed errori. Cambia solo il colore d'accento (blu, arancio, verde)
e l'icona.


#### Wallet e gas

Sotto l'header, fuori dal paper trading, la dashboard mostra il wallet del
bot: ETH per il gas (con il controvalore), USDC liberi, indirizzo con link a
Basescan e uno stato: **OK**, **IN ESAURIMENTO** (sotto `GAS_WARN_ETH`) o
**RICARICA ORA** (sotto la riserva minima). Lo stesso avviso compare nel
report Telegram del ciclo.

---


## 🎨 Icona del progetto

Gli asset sono in `static/`:

| File | Uso |
|------|-----|
| `icon.svg` | icona principale (vettoriale), logo in dashboard e README |
| `icon-small.svg` | variante semplificata, sorgente delle dimensioni piccole |
| `favicon.ico` | favicon multi-risoluzione (16, 32, 48 px) |
| `icon-192.png`, `icon-512.png` | PWA e condivisioni |
| `apple-touch-icon.png` | schermata home iOS |
| `site.webmanifest` | manifest PWA |
| `dashboard.css`, `dashboard.js` | design system condiviso con i bot degen e yield |

Le sorgenti sono gli SVG; i raster si rigenerano con `python tools/generate_icons.py`
(richiede `pip install cairosvg pillow`, dipendenze di solo sviluppo).

---

## ⚠️ Avvertenza

Questo software è sperimentale ed è fornito "così com'è", senza garanzie di alcun tipo
(vedi la licenza MIT). Non è consulenza finanziaria né un invito a investire.

- **Puoi perdere denaro.** Bug, decisioni sbagliate del modello, slippage, exploit dei protocolli,
  oracoli manipolati e liquidazioni possono far perdere in parte o del tutto il capitale.
- **Le decisioni le prende un LLM.** Può sbagliare o comportarsi in modo imprevedibile: i limiti
  dell'esecutore riducono il danno, non lo azzerano. I rendimenti passati, anche in paper, non
  garantiscono quelli futuri.
- **Parti in paper o dry-run.** In live usa un wallet dedicato al bot, con importi che puoi
  permetterti di perdere, e non riutilizzare quella chiave privata altrove.
- **Proteggi le chiavi.** La chiave privata va solo nelle variabili d'ambiente del deploy: non
  committarla mai. Senza `DASHBOARD_RUN_TOKEN` i comandi della dashboard restano disattivati:
  impostalo con un valore lungo e casuale prima di esporla su Internet.
- **Leggi e tasse.** Sei responsabile del rispetto delle norme e degli obblighi fiscali del tuo paese.
- **Ordini perp.** Qui `DRY_RUN` blocca solo gli swap on-chain (refuel, deposito sul Gate): gli ordini su SynFutures partono appena il microservizio ha la chiave. Per provare usa `PAPER_TRADING=true` (il `.env.example` parte così). Con la leva le perdite possono superare rapidamente il margine.

## 📜 Licenza
Distribuito sotto licenza MIT. Ispirato ad Alpha Arena e Rizzo AI Academy.
