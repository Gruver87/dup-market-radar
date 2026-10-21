"""Strict trade symbol / clientOid validation."""

import pytest
from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest, to_kucoin_symbol
from radar.trade.symbol_validate import (
    SymbolValidationError,
    normalize_trade_symbol,
    validate_client_oid,
    validate_currency,
    validate_order_id,
)


def test_normalize_accepts_common_forms():
    assert normalize_trade_symbol("btcusdt") == "BTCUSDT"
    assert normalize_trade_symbol("BTC-USDT") == "BTC-USDT"
    assert to_kucoin_symbol("BTCUSDT") == "BTC-USDT"


def test_normalize_refuses_junk():
    with pytest.raises(SymbolValidationError):
        normalize_trade_symbol("a")
    with pytest.raises(SymbolValidationError):
        normalize_trade_symbol("BTC<script>")
    with pytest.raises(SymbolValidationError):
        normalize_trade_symbol("X" * 40)
    with pytest.raises(SymbolValidationError):
        normalize_trade_symbol("../etc/passwd")


def test_client_oid_charset():
    assert validate_client_oid("dup-oid-zzzzzzzz") == "dup-oid-zzzzzzzz"
    with pytest.raises(SymbolValidationError):
        validate_client_oid("bad oid!")
    with pytest.raises(SymbolValidationError):
        validate_client_oid("short")


def test_order_id_refuses_path_injection():
    assert validate_order_id("5bd6e9286d99522a52e458de") == "5bd6e9286d99522a52e458de"
    assert validate_order_id("fill-ord-1") == "fill-ord-1"
    with pytest.raises(SymbolValidationError):
        validate_order_id("../etc/passwd")
    with pytest.raises(SymbolValidationError):
        validate_order_id("abc/../x")
    with pytest.raises(SymbolValidationError):
        validate_order_id("ord?x=1")


def test_currency_refuses_path_injection():
    assert validate_currency("usdt") == "USDT"
    assert validate_currency("BTC") == "BTC"
    with pytest.raises(SymbolValidationError):
        validate_currency("../etc/passwd")
    with pytest.raises(SymbolValidationError):
        validate_currency("USDT?x=1")
    with pytest.raises(SymbolValidationError):
        validate_currency("US/DT")


def test_signed_path_refuse_traversal():
    t = KuCoinManualTrader(enforce_symbol_meta=False)
    with pytest.raises(RuntimeError, match="path refused"):
        t._assert_signed_path("/api/../evil")
    with pytest.raises(RuntimeError, match="path refused"):
        t._assert_signed_path("https://evil/api/v1")
    assert t._assert_signed_path("/api/v1/orders") == "/api/v1/orders"


def test_reconcile_fill_refuses_bad_order_id():
    t = KuCoinManualTrader(enforce_symbol_meta=False)
    fill = t.reconcile_fill("../evil")
    assert fill.ok is False
    assert "order_id" in fill.message.lower() or "charset" in fill.message.lower()


def test_place_refuses_bad_symbol():
    t = KuCoinManualTrader(enforce_symbol_meta=False)
    r = t.place(
        TradeRequest(
            symbol="BTC;DROP",
            side="buy",
            confirm=False,
            quote_usdt_micros=1_000_000,
        )
    )
    assert r.ok is False
    assert "symbol" in r.message.lower() or "charset" in r.message.lower()


def test_api_rejects_bad_symbol_body():
    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/order",
        json={
            "symbol": "BTC<script>",
            "side": "buy",
            "confirm": False,
            "quote_usdt_micros": 1_000_000,
        },
    )
    assert res.status_code == 422


def test_api_rejects_bad_sector_id():
    client = TestClient(app_mod.app)
    res = client.get("/api/sector/../etc")
    assert res.status_code in {400, 404, 422}
