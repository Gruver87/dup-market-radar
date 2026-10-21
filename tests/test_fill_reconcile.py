"""KuCoin fill parse + auto-engine size_basis from reconcile."""

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import (
    FillInfo,
    KuCoinManualTrader,
    TradeResult,
    extract_order_id,
    parse_order_fill,
)


def test_extract_order_id():
    assert extract_order_id({"code": "200000", "data": {"orderId": "abc123"}}) == "abc123"
    assert extract_order_id({"data": {"id": "xyz"}}) == "xyz"
    assert extract_order_id(None) is None
    assert extract_order_id({}) is None


def test_parse_order_fill_net_of_base_fee():
    order = {
        "id": "ord1",
        "symbol": "SOL-USDT",
        "dealSize": "1.003",
        "dealFunds": "150.45",
        "fee": "0.003",
        "feeCurrency": "SOL",
        "isActive": False,
    }
    fill = parse_order_fill(order)
    assert fill.ok is True
    assert fill.size_basis == "kucoin_fill"
    # 1.003 - 0.003 = 1.000 → 100_000_000 base units
    assert fill.base_size_i == 100_000_000
    assert fill.fee_base_i == 300_000
    assert fill.deal_funds_micros == 150_450_000


def test_parse_order_fill_quote_fee_keeps_deal_size():
    order = {
        "id": "ord2",
        "symbol": "BTC-USDT",
        "dealSize": "0.01",
        "dealFunds": "650.0",
        "fee": "0.65",
        "feeCurrency": "USDT",
    }
    fill = parse_order_fill(order)
    assert fill.ok is True
    assert fill.base_size_i == 1_000_000  # 0.01 * 1e8
    assert fill.fee_base_i == 0


def test_parse_order_fill_zero_deal_refuses():
    fill = parse_order_fill({"id": "x", "dealSize": "0", "symbol": "ETH-USDT"})
    assert fill.ok is False
    assert fill.base_size_i == 0


def test_auto_buy_uses_reconciled_size(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    class FakeTrader(KuCoinManualTrader):
        def place(self, req):  # type: ignore[override]
            return TradeResult(
                True,
                False,
                "order accepted",
                {},
                {"code": "200000", "data": {"orderId": "fill-ord-1"}},
            )

        def reconcile_fill(self, order_id, **kwargs):  # type: ignore[override]
            assert order_id == "fill-ord-1"
            return FillInfo(
                ok=True,
                order_id=order_id,
                base_size_i=42_000_000,
                deal_funds_micros=10_000_000,
                fee_base_i=1000,
                size_basis="kucoin_fill",
                message="reconciled",
            )

    eng = AutoTradeEngine(
        FakeTrader(),
        lambda: {
            "ok": True,
            "mtf_ensure_ok": True,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "AAAUSDT",
                        "setup": "buy-watch",
                        "score_centi": 8000,
                        "price_i": 100_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            }
        },
    )
    eng._persist_ok = True
    eng._positions = {}
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": False,
                "fill_reconcile": True,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "sell_setups": ["fade-watch"],
                "min_score_centi": 5000,
                "quote_usdt_micros_per_buy": 10_000_000,
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
                "max_sells_per_cycle": 5,
                "stop_loss_bps": 1200,
                "require_no_risk_flags": True,
                "positions_backup_keep": 3,
            }
        },
    )
    summary = eng.run_once()
    assert any(a.get("ok") for a in summary["actions"])
    pos = eng._positions["AAAUSDT"]
    assert pos["base_size_i"] == 42_000_000
    assert pos["size_basis"] == "kucoin_fill"
    assert pos["order_id"] == "fill-ord-1"


def test_auto_buy_falls_back_to_estimate_on_reconcile_fail(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    class FakeTrader(KuCoinManualTrader):
        def place(self, req):  # type: ignore[override]
            return TradeResult(
                True,
                False,
                "order accepted",
                {},
                {"code": "200000", "data": {"orderId": "bad-ord"}},
            )

        def reconcile_fill(self, order_id, **kwargs):  # type: ignore[override]
            return FillInfo(ok=False, order_id=order_id, message="timeout")

    eng = AutoTradeEngine(
        FakeTrader(),
        lambda: {
            "ok": True,
            "mtf_ensure_ok": True,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "BBBUSDT",
                        "setup": "buy-watch",
                        "score_centi": 9000,
                        "price_i": 100_000_000,  # $1
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            }
        },
    )
    eng._persist_ok = True
    eng._positions = {}
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": False,
                "fill_reconcile": True,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "sell_setups": ["fade-watch"],
                "min_score_centi": 5000,
                "quote_usdt_micros_per_buy": 10_000_000,  # 10 USDT
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
                "max_sells_per_cycle": 5,
                "stop_loss_bps": 1200,
                "require_no_risk_flags": True,
                "positions_backup_keep": 3,
            }
        },
    )
    eng.run_once()
    pos = eng._positions["BBBUSDT"]
    assert pos["base_size_i"] > 0
    assert pos["size_basis"] == "estimated_fee_adjusted_unreconciled"
