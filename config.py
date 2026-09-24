"""
Configurazione per Intelligent Trading Agent (Base Chain).

Include:
- Connessione RPC a Base (Chain ID: 8453)
- Gestione contratti token (USDC, WETH, USDT)
- Integrazione Uniswap V3 (Auto-swap / Refuel ETH -> USDC)
- Integrazione SynFutures V3 Gate (Auto-deposito del collaterale)
- Limiti di gas, slippage e sicurezza
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on", "si")


# ---------------------------------------------------------------- Rete Base (8453)
CHAIN_ID = 8453
BASE_RPC_URL = (
    os.getenv("BASE_RPC")
    or os.getenv("BASE_RPC_URL")
    or "https://mainnet.base.org"
)
WALLET_ADDRESS = os.getenv("SYNFUTURES_WALLET") or os.getenv("WALLET_ADDRESS", "")
PRIVATE_KEY = os.getenv("SYNFUTURES_PRIVATE_KEY") or os.getenv("PRIVATE_KEY", "")

# ---------------------------------------------------------------- SynFutures Service
SYNFUTURES_SERVICE_URL = (
    os.getenv("SYNFUTURES_SERVICE_URL")
    or "http://localhost:3100"
).rstrip("/")
SYNFUTURES_API_KEY = os.getenv("API_KEY", "")

# ---------------------------------------------------------------- Contratti Base
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDT = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
CBBTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"

# Uniswap V3 su Base
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
UNISWAP_V3_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
FEE_TIERS = (100, 500, 3000, 10000)

# SynFutures V3 Gate (contratto di deposito collaterale su Base)
SYNFUTURES_GATE = os.getenv("SYNFUTURES_GATE") or "0x208B443983D8BcC8578e9D86Db23FbA547071270"

KNOWN_ASSETS = {
    "USDC": {"address": USDC, "decimals": 6, "stable": True},
    "WETH": {"address": WETH, "decimals": 18, "stable": False},
    "USDT": {"address": USDT, "decimals": 6, "stable": True},
    "CBBTC": {"address": CBBTC, "decimals": 8, "stable": False},
}

# ---------------------------------------------------------------- Modalita'
DRY_RUN = _b("DRY_RUN", False)

# ---------------------------------------------------------------- Esecuzione & Gas
MIN_ETH_RESERVE = _f("MIN_ETH_RESERVE", 0.001)
MAX_SLIPPAGE_BPS = _i("MAX_SLIPPAGE_BPS", 100)
DEFAULT_SLIPPAGE_BPS = _i("DEFAULT_SLIPPAGE_BPS", 50)
MAX_GAS_PRICE_GWEI = _f("MAX_GAS_PRICE_GWEI", 2.0)
TX_DEADLINE_SECONDS = _i("TX_DEADLINE_SECONDS", 300)
TX_TIMEOUT_SECONDS = _i("TX_TIMEOUT_SECONDS", 180)
HTTP_TIMEOUT = _i("HTTP_TIMEOUT", 30)

# ---------------------------------------------------------------- Auto-Refuel USDC (da ETH su Uniswap V3)
AUTO_SWAP_ETH_TO_USDC = _b("AUTO_SWAP_ETH_TO_USDC", True)
ETH_GAS_RESERVE = _f("ETH_GAS_RESERVE", 0.002)           # Riserva minima di ETH nativo per pagare le gas fee
MIN_ETH_SWAP_AMOUNT = _f("MIN_ETH_SWAP_AMOUNT", 0.002)    # Minimo di ETH spendibile per avviare uno swap
USDC_AUTO_SWAP_THRESHOLD = _f("USDC_AUTO_SWAP_THRESHOLD", 5.0)  # Se wallet USDC < soglia, prova auto-refuel

# ---------------------------------------------------------------- Auto-Deposito Gate SynFutures
AUTO_DEPOSIT_GATE = _b("AUTO_DEPOSIT_GATE", True)         # Deposita automaticamente USDC sul Gate se insufficiente
GATE_MARGIN_BUFFER = _f("GATE_MARGIN_BUFFER", 1.20)       # Cuscinetto sicurezza (+20%) sul margine richiesto
MIN_GATE_DEPOSIT = _f("MIN_GATE_DEPOSIT", 1.0)            # Deposito minimo in USDC
MIN_NOTIONAL_USD = _f("MIN_NOTIONAL_USD", 70.0)           # Nozionale minimo SynFutures V3 su Base
