import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Assicura import dalla root del progetto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from base_client import BaseClient
from synfutures_trader import SynFuturesTrader
from tools.refuel import get_wallet_balances
from uniswap import UniswapV3, Route


class TestRefuelGate(unittest.TestCase):
    def setUp(self):
        config.DRY_RUN = True
        config.AUTO_SWAP_ETH_TO_USDC = True
        config.AUTO_DEPOSIT_GATE = True
        config.USDC_AUTO_SWAP_THRESHOLD = 5.0
        config.ETH_GAS_RESERVE = 0.002
        config.MIN_ETH_SWAP_AMOUNT = 0.002
        config.GATE_MARGIN_BUFFER = 1.20

    def test_config_defaults(self):
        self.assertEqual(config.CHAIN_ID, 8453)
        self.assertTrue(config.USDC.startswith("0x"))
        self.assertTrue(config.WETH.startswith("0x"))
        self.assertTrue(config.SYNFUTURES_GATE.startswith("0x"))
        self.assertTrue(config.AUTO_SWAP_ETH_TO_USDC)
        self.assertTrue(config.AUTO_DEPOSIT_GATE)

    def test_get_wallet_balances_needs_refuel(self):
        client = MagicMock(spec=BaseClient)
        client.address = "0x1111111111111111111111111111111111111111"
        client.eth_balance.return_value = 0.05
        client.balance_of_float.side_effect = lambda token, *args: 0.0 if token == config.WETH else 1.0  # USDC = 1.0

        info = get_wallet_balances(client)
        self.assertEqual(info["eth_balance"], 0.05)
        self.assertEqual(info["usdc_balance"], 1.0)
        self.assertAlmostEqual(info["swappable_native_eth"], 0.048, places=4)
        self.assertTrue(info["needs_refuel"])

    def test_get_wallet_balances_no_refuel_when_sufficient(self):
        client = MagicMock(spec=BaseClient)
        client.address = "0x1111111111111111111111111111111111111111"
        client.eth_balance.return_value = 0.05
        client.balance_of_float.side_effect = lambda token, *args: 0.0 if token == config.WETH else 20.0  # USDC = 20.0

        info = get_wallet_balances(client)
        self.assertEqual(info["usdc_balance"], 20.0)
        self.assertFalse(info["needs_refuel"])

    def test_auto_refuel_usdc_execution(self):
        client = MagicMock(spec=BaseClient)
        client.w3 = MagicMock()
        client.eth_balance.return_value = 0.02
        client.balance_of_float.side_effect = lambda token, *args: 0.0 if token == config.WETH else 2.0  # USDC = 2.0
        client.balance_of.return_value = 0

        uniswap = UniswapV3(client)
        fake_route = Route(
            tokens=[config.WETH, config.USDC],
            fees=[500],
            amount_in=int(0.018 * 1e18),
            amount_out=int(50 * 1e6),
            gas_estimate=150000,
        )
        uniswap.best_route = MagicMock(return_value=fake_route)
        uniswap.swap = MagicMock(return_value={"tx_hash": "0xabc", "description": "swap test"})

        res = uniswap.auto_refuel_usdc()
        self.assertIsNotNone(res)
        self.assertEqual(res["tx_hash"], "0xabc")
        uniswap.swap.assert_called_once()

    @patch.object(SynFuturesTrader, "_check_service_health")
    def test_ensure_gate_margin_auto_deposit(self, mock_health):
        mock_health.return_value = None
        dummy_pk = "0x" + "1" * 64
        dummy_addr = "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"
        trader = SynFuturesTrader(
            secret_key=dummy_pk,
            account_address=dummy_addr,
            service_url="http://localhost:3100",
        )

        trader.get_account_status = MagicMock(side_effect=[
            {"balance_usd": 0.0, "open_positions": []},  # Before deposit
            {"balance_usd": 30.0, "open_positions": []}, # After deposit
        ])
        trader.deposit_usdc = MagicMock(return_value={"txHash": "0x123"})
        trader.ensure_usdc_balance = MagicMock(return_value=None)

        # Mock client wallet USDC
        trader.client = MagicMock(spec=BaseClient)
        trader.client.balance_of_float.return_value = 50.0

        res = trader.ensure_gate_margin(required_margin_usd=25.0)
        self.assertEqual(res["status"], "deposited")
        self.assertGreater(res["deposited"], 0)
        self.assertEqual(res["gate_balance"], 30.0)
        trader.deposit_usdc.assert_called_once()

    @patch.object(SynFuturesTrader, "_check_service_health")
    def test_execute_signal_triggers_gate_margin_when_empty(self, mock_health):
        mock_health.return_value = None
        dummy_pk = "0x" + "1" * 64
        dummy_addr = "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"
        trader = SynFuturesTrader(
            secret_key=dummy_pk,
            account_address=dummy_addr,
            service_url="http://localhost:3100",
        )

        # Simulate Gate 0 initially, then 35 after ensure_gate_margin
        trader.get_account_status = MagicMock(side_effect=[
            {"balance_usd": 0.0, "open_positions": []},
            {"balance_usd": 35.0, "open_positions": []},
        ])
        trader.ensure_gate_margin = MagicMock(return_value={"status": "deposited", "gate_balance": 35.0})
        trader._make_request = MagicMock(return_value={"orderId": "12345", "status": "filled"})

        signal = {
            "operation": "open",
            "symbol": "BTC",
            "direction": "long",
            "target_portion_of_balance": 0.5,
            "leverage": 2.0,
            "stop_loss_percent": 2.0,
            "reason": "Test AI order"
        }

        res = trader.execute_signal(signal)
        trader.ensure_gate_margin.assert_called_once()
        trader._make_request.assert_called_with("POST", "/order/market", unittest.mock.ANY)
        self.assertEqual(res["orderId"], "12345")


if __name__ == "__main__":
    unittest.main()
