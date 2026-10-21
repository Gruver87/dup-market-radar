"""Binance spot must drop BREAK (delisted) — empty windowed klines otherwise."""

from radar.ingest.binance import (
    filter_rows_to_trading,
    trading_symbols_from_exchange_info,
)


def test_trading_symbols_from_exchange_info():
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "NFPUSDT", "status": "BREAK"},
            {"symbol": "ETHUSDT", "status": "TRADING"},
            {"not": "a symbol"},
            None,
        ]
    }
    assert trading_symbols_from_exchange_info(info) == {"BTCUSDT", "ETHUSDT"}


def test_filter_rows_drops_break():
    trading = {"BTCUSDT", "ETHUSDT"}
    rows = [
        {"symbol": "NFPUSDT", "venue": "binance"},
        {"symbol": "BTCUSDT", "venue": "binance"},
        {"symbol": "VICUSDT", "venue": "binance"},
    ]
    out = filter_rows_to_trading(rows, trading)
    assert [r["symbol"] for r in out] == ["BTCUSDT"]


def test_filter_rows_empty_trading_keeps_all():
    rows = [{"symbol": "NFPUSDT"}]
    assert filter_rows_to_trading(rows, set()) == rows
