"""
Accesso alla chain Base: connessione RPC, lettura ERC-20, invio transazioni e interazione diretta con SynFutures Gate.

Include:
- Lettura saldi ETH nativo, WETH, USDC
- Wrap / Unwrap di ETH <-> WETH
- Controllo allowance ed approvazione ERC-20
- Interazione diretta con il contratto Gate di SynFutures (deposit / withdraw / reserveOf)
- Gestione gas, sicurezza e dry-run
"""

import logging
import time
from typing import Any, Dict, List, Optional

from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

import config

logger = logging.getLogger(__name__)

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True,
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False,
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

MAX_UINT256 = 2 ** 256 - 1

GATE_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "arg", "type": "bytes32"}],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "arg", "type": "bytes32"}],
        "name": "withdraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "quote", "type": "address"},
            {"internalType": "address", "name": "user", "type": "address"},
        ],
        "name": "reserveOf",
        "outputs": [{"internalType": "uint256", "name": "balance", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

WETH_ABI = [
    {
        "constant": False,
        "inputs": [],
        "name": "deposit",
        "outputs": [],
        "payable": True,
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "wad", "type": "uint256"}],
        "name": "withdraw",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def encode_gate_param(token_address: str, amount_raw: int) -> bytes:
    """
    Codifica i parametri per deposit e withdraw sul Gate di SynFutures:
    96-bit (12 bytes) uint96 amount + 160-bit (20 bytes) address token = 32 bytes (bytes32).
    """
    token_clean = Web3.to_checksum_address(token_address).lower().replace("0x", "")
    token_bytes = bytes.fromhex(token_clean)
    amount_bytes = int(amount_raw).to_bytes(12, byteorder="big")
    return amount_bytes + token_bytes


class BaseChainError(RuntimeError):
    """Errore non recuperabile nell'interazione con la chain."""


class BaseClient:
    def __init__(
        self,
        rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        address: Optional[str] = None,
    ):
        self.rpc_url = rpc_url or config.BASE_RPC_URL
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": config.HTTP_TIMEOUT}))
        try:
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass

        self._account = None
        pk = private_key or config.PRIVATE_KEY
        if pk:
            if not pk.startswith("0x"):
                pk = "0x" + pk
            self._account = Account.from_key(pk)
            self.address = self._account.address
        elif address or config.WALLET_ADDRESS:
            raw_addr = address or config.WALLET_ADDRESS
            self.address = Web3.to_checksum_address(raw_addr)
        else:
            self.address = ""

        if self.address and self._account:
            configured = address or config.WALLET_ADDRESS
            if configured and configured.lower() != self.address.lower():
                raise BaseChainError(
                    f"PRIVATE_KEY corrisponde a {self.address}, ma e' configurato {configured}"
                )

        self._decimals_cache: Dict[str, int] = {}
        self._symbol_cache: Dict[str, str] = {}

    @property
    def has_signer(self) -> bool:
        return self._account is not None

    def is_connected(self) -> bool:
        try:
            return bool(self.w3.is_connected())
        except Exception:
            return False

    def chain_id(self) -> int:
        return self.w3.eth.chain_id

    def gas_price_gwei(self) -> float:
        return float(self.w3.from_wei(self.w3.eth.gas_price, "gwei"))

    def eth_balance(self, address: str = None) -> float:
        addr = Web3.to_checksum_address(address or self.address)
        wei = self.w3.eth.get_balance(addr)
        return float(self.w3.from_wei(wei, "ether"))

    # ------------------------------------------------------------ ERC-20
    def erc20(self, token_address: str):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )

    def decimals(self, token_address: str) -> int:
        key = token_address.lower()
        if key not in self._decimals_cache:
            self._decimals_cache[key] = int(self.erc20(token_address).functions.decimals().call())
        return self._decimals_cache[key]

    def symbol(self, token_address: str) -> str:
        key = token_address.lower()
        if key not in self._symbol_cache:
            try:
                self._symbol_cache[key] = str(self.erc20(token_address).functions.symbol().call())
            except Exception:
                self._symbol_cache[key] = "???"
        return self._symbol_cache[key]

    def balance_of(self, token_address: str, address: str = None) -> int:
        addr = Web3.to_checksum_address(address or self.address)
        return int(self.erc20(token_address).functions.balanceOf(addr).call())

    def balance_of_float(self, token_address: str, address: str = None) -> float:
        raw = self.balance_of(token_address, address)
        return raw / (10 ** self.decimals(token_address))

    def allowance(self, token_address: str, spender: str, address: str = None) -> int:
        addr = Web3.to_checksum_address(address or self.address)
        return int(
            self.erc20(token_address)
            .functions.allowance(addr, Web3.to_checksum_address(spender))
            .call()
        )

    # ------------------------------------------------------------ SynFutures Gate
    def gate_contract(self):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(config.SYNFUTURES_GATE),
            abi=GATE_ABI,
        )

    def gate_balance_of(self, token_address: str, address: str = None) -> int:
        addr = Web3.to_checksum_address(address or self.address)
        token = Web3.to_checksum_address(token_address)
        return int(self.gate_contract().functions.reserveOf(token, addr).call())

    def gate_balance_of_float(self, token_address: str, address: str = None) -> float:
        raw = self.gate_balance_of(token_address, address)
        return raw / (10 ** self.decimals(token_address))

    def deposit_to_gate(self, token_address: str, amount_float: float) -> Dict[str, Any]:
        """
        Approva il Gate di SynFutures e deposita il token (es. USDC).
        """
        token = Web3.to_checksum_address(token_address)
        amount_raw = int(amount_float * (10 ** self.decimals(token_address)))
        if amount_raw <= 0:
            raise BaseChainError("Importo di deposito non valido")

        allow_res = self.ensure_allowance(token, config.SYNFUTURES_GATE, amount_raw)

        param = encode_gate_param(token, amount_raw)
        tx = self.gate_contract().functions.deposit(param).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": config.CHAIN_ID,
            "value": 0,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        sym = self.symbol(token_address)
        res = self.send_transaction(tx, description=f"deposito Gate ({amount_float:.4f} {sym})")
        if allow_res:
            res["approval_tx"] = allow_res
        return res

    def withdraw_from_gate(self, token_address: str, amount_float: float) -> Dict[str, Any]:
        """
        Ritira il token dal Gate di SynFutures nel wallet.
        """
        token = Web3.to_checksum_address(token_address)
        amount_raw = int(amount_float * (10 ** self.decimals(token_address)))
        if amount_raw <= 0:
            raise BaseChainError("Importo di ritiro non valido")

        param = encode_gate_param(token, amount_raw)
        tx = self.gate_contract().functions.withdraw(param).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": config.CHAIN_ID,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        sym = self.symbol(token_address)
        return self.send_transaction(tx, description=f"ritiro Gate ({amount_float:.4f} {sym})")

    # ------------------------------------------------------------ WETH wrap/unwrap
    def wrap_eth(self, eth_amount: float) -> Dict[str, Any]:
        """Deposita ETH nativo nel contratto WETH canonico su Base (1:1)."""
        self._require_signer()
        amount_wei = int(eth_amount * 1e18)
        if amount_wei <= 0:
            raise BaseChainError("Importo ETH non valido per wrap")

        weth_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.WETH), abi=WETH_ABI
        )
        tx = weth_contract.functions.deposit().build_transaction({
            "from": self.address,
            "value": amount_wei,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": config.CHAIN_ID,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        return self.send_transaction(tx, description=f"wrap {eth_amount:.5f} ETH -> WETH")

    def unwrap_weth(self, weth_amount: float) -> Dict[str, Any]:
        """Ritira WETH per ricevere ETH nativo 1:1."""
        self._require_signer()
        amount_wei = int(weth_amount * 1e18)
        if amount_wei <= 0:
            raise BaseChainError("Importo WETH non valido per unwrap")

        weth_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.WETH), abi=WETH_ABI
        )
        tx = weth_contract.functions.withdraw(amount_wei).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": config.CHAIN_ID,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        return self.send_transaction(tx, description=f"unwrap {weth_amount:.5f} WETH -> ETH")

    # ------------------------------------------------------------ Transazioni
    def _require_signer(self):
        if not self._account:
            raise BaseChainError("Operazione di scrittura richiede PRIVATE_KEY configurata")

    def _check_gas_price(self):
        gp = self.gas_price_gwei()
        if gp > config.MAX_GAS_PRICE_GWEI:
            raise BaseChainError(
                f"Gas price corrente ({gp:.2f} gwei) supera il massimo consentito ({config.MAX_GAS_PRICE_GWEI:.2f} gwei)"
            )

    def send_transaction(self, tx: Dict[str, Any], description: str = "") -> Dict[str, Any]:
        self._require_signer()
        self._check_gas_price()

        if "gas" not in tx:
            try:
                estimated = self.w3.eth.estimate_gas(tx)
                tx["gas"] = int(estimated * 1.25)
            except Exception as e:
                raise BaseChainError(f"Stima gas fallita per {description or 'tx'}: {e}")

        if "gasPrice" not in tx and "maxFeePerGas" not in tx:
            tx["gasPrice"] = int(self.w3.eth.gas_price * 1.1)

        if config.DRY_RUN:
            logger.info("[DRY-RUN] Transazione non firmata: %s (gas=%s)", description, tx.get("gas"))
            return {
                "dry_run": True,
                "tx_hash": None,
                "description": description,
                "gas_estimate": tx.get("gas"),
            }

        signed = self._account.sign_transaction(tx)
        raw_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash = raw_hash.hex()
        logger.info("Transazione inviata: %s -> tx: %s", description, tx_hash)

        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(
                raw_hash, timeout=config.TX_TIMEOUT_SECONDS
            )
        except Exception as e:
            raise BaseChainError(f"Timeout o errore attesa conferma per {tx_hash}: {e}")

        status = int(receipt.get("status", 0))
        if status != 1:
            raise BaseChainError(f"Transazione fallita on-chain (revert): {tx_hash}")

        cost_eth = float(self.w3.from_wei(receipt.gasUsed * receipt.effectiveGasPrice, "ether"))
        return {
            "dry_run": False,
            "tx_hash": tx_hash,
            "status": "success",
            "block_number": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
            "cost_eth": cost_eth,
            "explorer": f"https://basescan.org/tx/{tx_hash}",
            "description": description,
        }

    def ensure_allowance(self, token_address: str, spender: str, amount_raw: int) -> Optional[Dict[str, Any]]:
        current = self.allowance(token_address, spender)
        if current >= amount_raw:
            return None

        sym = self.symbol(token_address)
        logger.info("Approvazione %s per spender %s...", sym, spender)
        token = self.erc20(token_address)
        tx = token.functions.approve(
            Web3.to_checksum_address(spender), MAX_UINT256
        ).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": config.CHAIN_ID,
        })
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        return self.send_transaction(tx, description=f"approve {sym} -> {spender}")

    def deadline(self) -> int:
        return int(time.time()) + config.TX_DEADLINE_SECONDS
