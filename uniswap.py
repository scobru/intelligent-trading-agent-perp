"""
Rotte e swap su Uniswap V3 (Base), senza aggregatori esterni.

Fornisce:
- Calcolo rotte e preventivi (QuoterV2)
- Esecuzione swap (SwapRouter02)
- swap_eth_to_usdc: conversione diretta o con wrap automatico ETH -> WETH -> USDC
- auto_refuel_usdc: controllo automatico saldi wallet e refuel USDC se sotto soglia
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from web3 import Web3

import config
from base_client import BaseClient, BaseChainError

logger = logging.getLogger(__name__)

QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes", "name": "path", "type": "bytes"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        ],
        "name": "quoteExactInput",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160[]", "name": "sqrtPriceX96AfterList", "type": "uint160[]"},
            {"internalType": "uint32[]", "name": "initializedTicksCrossedList", "type": "uint32[]"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

SWAP_ROUTER_02_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "bytes", "name": "path", "type": "bytes"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                ],
                "internalType": "struct IV3SwapRouter.ExactInputParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInput",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def encode_path(tokens: List[str], fees: List[int]) -> bytes:
    """
    Codifica una rotta multi-hop nel formato Uniswap V3:
    tokenA (20 bytes) + fee1 (3 bytes) + tokenB (20 bytes) + fee2 (3 bytes) + tokenC (20 bytes)...
    """
    if len(tokens) != len(fees) + 1:
        raise ValueError("tokens must have len(fees) + 1 elements")
    out = bytearray()
    for i, token in enumerate(tokens):
        token_clean = Web3.to_checksum_address(token).lower().replace("0x", "")
        out.extend(bytes.fromhex(token_clean))
        if i < len(fees):
            out.extend(int(fees[i]).to_bytes(3, byteorder="big"))
    return bytes(out)


@dataclass
class Route:
    tokens: List[str]
    fees: List[int]
    amount_in: int
    amount_out: int
    gas_estimate: int

    @property
    def is_single_hop(self) -> bool:
        return len(self.fees) == 1

    @property
    def path(self) -> bytes:
        return encode_path(self.tokens, self.fees)

    def min_amount_out(self, slippage_bps: int) -> int:
        return int(self.amount_out * (10_000 - slippage_bps) // 10_000)

    def describe(self, client: BaseClient) -> str:
        names = [client.symbol(t) for t in self.tokens]
        hops = []
        for i, fee in enumerate(self.fees):
            hops.append(f"{names[i]} -[{fee / 10000:.2f}%]-> {names[i + 1]}")
        return " -> ".join(hops)


class UniswapV3:
    def __init__(self, client: BaseClient):
        self.client = client
        self.w3 = client.w3
        self.quoter = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.UNISWAP_V3_QUOTER_V2), abi=QUOTER_V2_ABI
        )
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.UNISWAP_V3_SWAP_ROUTER_02), abi=SWAP_ROUTER_02_ABI
        )
        self.factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.UNISWAP_V3_FACTORY), abi=FACTORY_ABI
        )

    def pool_exists(self, token_a: str, token_b: str, fee: int) -> bool:
        try:
            pool = self.factory.functions.getPool(
                Web3.to_checksum_address(token_a),
                Web3.to_checksum_address(token_b),
                fee,
            ).call()
            return pool != "0x0000000000000000000000000000000000000000"
        except Exception:
            return False

    def quote_single(self, token_in: str, token_out: str, fee: int, amount_in: int) -> Optional[Tuple[int, int]]:
        try:
            params = {
                "tokenIn": Web3.to_checksum_address(token_in),
                "tokenOut": Web3.to_checksum_address(token_out),
                "amountIn": amount_in,
                "fee": fee,
                "sqrtPriceLimitX96": 0,
            }
            res = self.quoter.functions.quoteExactInputSingle(params).call()
            return int(res[0]), int(res[3])
        except Exception:
            return None

    def quote_path(self, tokens: List[str], fees: List[int], amount_in: int) -> Optional[Tuple[int, int]]:
        try:
            path_bytes = encode_path(tokens, fees)
            res = self.quoter.functions.quoteExactInput(path_bytes, amount_in).call()
            return int(res[0]), int(res[3])
        except Exception:
            return None

    def best_route(self, token_in: str, token_out: str, amount_in: int) -> Optional[Route]:
        candidates: List[Route] = []

        # 1. Rotte single-hop
        for fee in config.FEE_TIERS:
            q = self.quote_single(token_in, token_out, fee, amount_in)
            if q and q[0] > 0:
                candidates.append(
                    Route(
                        tokens=[token_in, token_out],
                        fees=[fee],
                        amount_in=amount_in,
                        amount_out=q[0],
                        gas_estimate=q[1],
                    )
                )

        # 2. Rotte a due salti via WETH se non sono gia' token di entrata/uscita
        if token_in.lower() != config.WETH.lower() and token_out.lower() != config.WETH.lower():
            for fee1 in (500, 3000):
                for fee2 in (500, 3000):
                    q = self.quote_path([token_in, config.WETH, token_out], [fee1, fee2], amount_in)
                    if q and q[0] > 0:
                        candidates.append(
                            Route(
                                tokens=[token_in, config.WETH, token_out],
                                fees=[fee1, fee2],
                                amount_in=amount_in,
                                amount_out=q[0],
                                gas_estimate=q[1],
                            )
                        )

        if not candidates:
            return None
        return max(candidates, key=lambda r: r.amount_out)

    def swap(self, route: Route, slippage_bps: int = None, recipient: str = None) -> Dict[str, Any]:
        slippage = slippage_bps if slippage_bps is not None else config.DEFAULT_SLIPPAGE_BPS
        min_out = route.min_amount_out(slippage)
        rcpt = Web3.to_checksum_address(recipient or self.client.address)
        token_in = route.tokens[0]

        # Ensure allowance per SwapRouter02
        self.client.ensure_allowance(
            token_in, config.UNISWAP_V3_SWAP_ROUTER_02, route.amount_in
        )

        nonce = self.client.w3.eth.get_transaction_count(self.client.address)
        if route.is_single_hop:
            params = {
                "tokenIn": Web3.to_checksum_address(route.tokens[0]),
                "tokenOut": Web3.to_checksum_address(route.tokens[1]),
                "fee": route.fees[0],
                "recipient": rcpt,
                "amountIn": route.amount_in,
                "amountOutMinimum": min_out,
                "sqrtPriceLimitX96": 0,
            }
            fn = self.router.functions.exactInputSingle(params)
        else:
            params = {
                "path": route.path,
                "recipient": rcpt,
                "amountIn": route.amount_in,
                "amountOutMinimum": min_out,
            }
            fn = self.router.functions.exactInput(params)

        tx = fn.build_transaction({
            "from": self.client.address,
            "nonce": nonce,
            "chainId": config.CHAIN_ID,
            "value": 0,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)

        result = self.client.send_transaction(
            tx, description=f"swap {route.describe(self.client)}"
        )
        result.update({
            "amount_in": route.amount_in,
            "expected_out": route.amount_out,
            "min_out": min_out,
            "slippage_bps": slippage,
        })
        return result

    # ------------------------------------------------------------ auto-refuel USDC
    def swap_eth_to_usdc(self, eth_amount: float, slippage_bps: int = None) -> Dict[str, Any]:
        """
        Converte un ammontare di ETH in USDC:
        1. Se il wallet ha gia' abbastanza WETH, usa WETH. Altrimenti wrappa l'ETH nativo mancante in WETH (1:1).
        2. Esegue swap Uniswap V3 WETH -> USDC.
        """
        if eth_amount <= 0:
            raise BaseChainError("Importo ETH non valido per swap in USDC")

        slippage_bps = slippage_bps or config.DEFAULT_SLIPPAGE_BPS
        amount_wei = int(eth_amount * 1e18)

        weth_raw = self.client.balance_of(config.WETH)
        wrap_needed = amount_wei - weth_raw

        wrap_res = None
        if wrap_needed > 0:
            wrap_eth_amount = wrap_needed / 1e18
            logger.info("Wrap di %.5f ETH in WETH per swap in USDC...", wrap_eth_amount)
            wrap_res = self.client.wrap_eth(wrap_eth_amount)

        route = self.best_route(config.WETH, config.USDC, amount_wei)
        if not route or route.amount_out <= 0:
            raise BaseChainError("Impossibile trovare una rotta Uniswap V3 per WETH -> USDC")

        swap_res = self.swap(route, slippage_bps=slippage_bps)
        swap_res["wrap_tx"] = wrap_res.get("tx_hash") if wrap_res else None
        swap_res["eth_swapped"] = eth_amount
        usdc_est = route.amount_out / (10 ** 6)
        logger.info(
            "Swap completato: %.5f ETH -> ~%.2f USDC (tx: %s)",
            eth_amount, usdc_est, swap_res.get("tx_hash")
        )
        return swap_res

    def auto_refuel_usdc(
        self,
        gas_reserve: float = None,
        min_swap_eth: float = None,
        threshold_usdc: float = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Se il saldo USDC nel wallet e' sotto la soglia (default config.USDC_AUTO_SWAP_THRESHOLD),
        e c'e' ETH nativo in eccesso rispetto alla riserva per gas (default config.ETH_GAS_RESERVE),
        swappa in automatico l'eccesso di ETH in USDC tenendosi l'ETH necessario per le fee.
        """
        if not getattr(config, "AUTO_SWAP_ETH_TO_USDC", True):
            return None

        gas_reserve = gas_reserve if gas_reserve is not None else getattr(config, "ETH_GAS_RESERVE", 0.002)
        min_swap_eth = min_swap_eth if min_swap_eth is not None else getattr(config, "MIN_ETH_SWAP_AMOUNT", 0.002)
        threshold_usdc = threshold_usdc if threshold_usdc is not None else getattr(config, "USDC_AUTO_SWAP_THRESHOLD", 5.0)

        usdc_bal = self.client.balance_of_float(config.USDC)
        if usdc_bal >= threshold_usdc:
            return None

        eth_bal = self.client.eth_balance()
        weth_bal = self.client.balance_of_float(config.WETH)

        swappable_native_eth = max(0.0, eth_bal - gas_reserve)
        total_swappable_eth = swappable_native_eth + weth_bal

        if total_swappable_eth < min_swap_eth:
            logger.info(
                "Auto-refuel USDC saltato: USDC=%.2f, ETH=%.5f, WETH=%.5f (riserva gas=%.5f, disponibile=%.5f < min=%.5f)",
                usdc_bal, eth_bal, weth_bal, gas_reserve, total_swappable_eth, min_swap_eth
            )
            return None

        logger.info(
            "Auto-refuel USDC attivato: USDC=%.2f < soglia=%.2f. ETH=%.5f (riserva gas=%.5f), WETH=%.5f -> swappo %.5f ETH/WETH in USDC",
            usdc_bal, threshold_usdc, eth_bal, gas_reserve, weth_bal, total_swappable_eth
        )
        try:
            return self.swap_eth_to_usdc(total_swappable_eth)
        except Exception as exc:
            logger.error("Errore durante auto-refuel ETH -> USDC: %s", exc)
            return None
