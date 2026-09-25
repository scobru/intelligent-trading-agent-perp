import pytest
import json
from unittest.mock import MagicMock, patch
import trading_agent
from synfutures_trader import SynFuturesTrader


def test_dashboard_imports_db_utils():
    import dashboard
    assert hasattr(dashboard, "db_utils"), "dashboard.py must have db_utils imported"


def test_clean_and_parse_json_strips_safety_preamble():
    raw_response = """User Safety: safe
Safety Categories: None
```json
{
    "operation": "hold",
    "symbol": "BTC",
    "direction": "long",
    "target_portion_of_balance": 0.0,
    "leverage": 1,
    "stop_loss_percent": 2.0,
    "reason": "Wait for better entry"
}
```"""
    data = trading_agent._clean_and_parse_json(raw_response, symbols=["BTC", "ETH"])
    assert data["operation"] == "hold"
    assert data["symbol"] == "BTC"
    assert data["target_portion_of_balance"] == 0.0


def test_clean_and_parse_json_converts_zero_portion_open_to_hold():
    raw = json.dumps({
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.0,
        "leverage": 2,
        "stop_loss_percent": 1.5,
        "reason": "Bullish setup but no balance"
    })
    data = trading_agent._clean_and_parse_json(raw, symbols=["BTC", "ETH"])
    assert data["operation"] == "hold"
    assert "Target portion pari a 0" in data["reason"]


def test_clean_and_parse_json_unauthorized_advice_without_json():
    raw = "User Safety: unsafe\nSafety Categories: Unauthorized Advice"
    with pytest.raises(ValueError, match="non contiene testo valido dopo la rimozione dei prefissi di sicurezza"):
        trading_agent._clean_and_parse_json(raw, symbols=["BTC", "ETH"])


def test_ensure_gate_margin_no_deposit_when_zero_wallet_usdc():
    trader = SynFuturesTrader(service_url="http://mock-service", account_address="0x14d0E25Bc1c094938c25984a06bbcDa6a632FA28")
    
    # Mock account status with 0 Gate balance
    trader.get_account_status = MagicMock(return_value={"balance_usd": 0.0})
    trader.client = MagicMock()
    trader.client.balance_of_float.return_value = 0.0
    trader.ensure_usdc_balance = MagicMock(return_value=None)
    trader.deposit_usdc = MagicMock()

    res = trader.ensure_gate_margin(required_margin_usd=25.0)
    assert res["status"] == "insufficient_wallet"
    assert res["deposited"] == 0.0
    trader.deposit_usdc.assert_not_called()


def test_execute_signal_rejects_open_when_zero_balance():
    trader = SynFuturesTrader(service_url="http://mock-service", account_address="0x14d0E25Bc1c094938c25984a06bbcDa6a632FA28")
    trader.get_account_status = MagicMock(return_value={"balance_usd": 0.0})
    trader.get_tradable_markets = MagicMock(return_value={"BTC": "BTC-USDC-LINK"})
    trader.ensure_gate_margin = MagicMock(return_value={"status": "insufficient_wallet"})

    order = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.5,
        "leverage": 2,
        "stop_loss_percent": 1.0,
        "reason": "Testing 0 balance rejection"
    }
    res = trader.execute_signal(order)
    assert res.get("status") == "rejected"
    assert "Saldo insufficiente" in res.get("message", "")


def test_execute_signal_catches_unhandled_exceptions():
    trader = SynFuturesTrader(service_url="http://mock-service", account_address="0x14d0E25Bc1c094938c25984a06bbcDa6a632FA28")
    trader._validate_order_input = MagicMock(side_effect=Exception("Critical network error"))

    order = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.5,
        "leverage": 2,
        "reason": "Testing error catch"
    }
    res = trader.execute_signal(order)
    assert res.get("status") == "error"
    assert "Critical network error" in res.get("message", "")


def test_synfutures_release_funds():
    trader = SynFuturesTrader(service_url="http://mock-service", account_address="0x14d0E25Bc1c094938c25984a06bbcDa6a632FA28")
    trader.get_account_status = MagicMock(return_value={
        "balance_usd": 30.0,
        "open_positions": [{"symbol": "BTC-USDC-LINK", "side": "LONG"}]
    })
    trader.withdraw_usdc = MagicMock(return_value={"status": "ok"})
    trader._make_request = MagicMock(return_value={"status": "closed"})
    trader.client = MagicMock()
    trader.client.balance_of_float.return_value = 30.0

    res = trader.release_funds(target_usdc=30.0)
    assert res["status"] == "success"
    assert res["withdrawn_usd"] == 30.0
    trader.withdraw_usdc.assert_called_with(30.0)

