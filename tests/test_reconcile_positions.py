"""Re-reconcile unreconciled auto positions."""

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import FillInfo, KuCoinManualTrader


def test_reconcile_updates_unreconciled(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    class T(KuCoinManualTrader):
        def reconcile_fill(self, order_id, **kwargs):  # type: ignore[override]
            return FillInfo(
                ok=True,
                order_id=order_id,
                base_size_i=77_000_000,
                deal_funds_micros=10_000_000,
                size_basis="kucoin_fill",
                message="reconciled",
            )

    eng = AutoTradeEngine(T(enforce_symbol_meta=False), lambda: {"sectors": {}})
    eng._persist_ok = True
    eng._positions = {
        "AAAUSDT": {
            "symbol": "AAAUSDT",
            "base_size_i": 50_000_000,
            "size_basis": "estimated_fee_adjusted_unreconciled",
            "order_id": "ord-re-1",
            "quote_usdt_micros": 10_000_000,
            "entry_price_i": 100_000_000,
        },
        "BBBUSDT": {
            "symbol": "BBBUSDT",
            "base_size_i": 10_000_000,
            "size_basis": "kucoin_fill",
            "order_id": "ord-done",
        },
    }
    out = eng.reconcile_positions()
    assert len(out["updated"]) == 1
    assert out["updated"][0]["symbol"] == "AAAUSDT"
    assert eng._positions["AAAUSDT"]["base_size_i"] == 77_000_000
    assert eng._positions["AAAUSDT"]["size_basis"] == "kucoin_fill"
    assert "BBBUSDT" in out["skipped"]


def test_reconcile_one_symbol_even_if_filled(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    class T(KuCoinManualTrader):
        def reconcile_fill(self, order_id, **kwargs):  # type: ignore[override]
            return FillInfo(
                ok=True,
                order_id=order_id,
                base_size_i=12_000_000,
                size_basis="kucoin_fill",
                message="ok",
            )

    eng = AutoTradeEngine(T(enforce_symbol_meta=False), lambda: {"sectors": {}})
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "base_size_i": 11_000_000,
            "size_basis": "kucoin_fill",
            "order_id": "ord-sol",
        }
    }
    out = eng.reconcile_positions(symbol="SOLUSDT")
    assert len(out["updated"]) == 1
    assert eng._positions["SOLUSDT"]["base_size_i"] == 12_000_000
