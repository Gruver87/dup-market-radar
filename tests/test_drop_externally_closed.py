from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_drop_externally_closed_removes_flat(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    emitted = []
    eng.set_on_action(lambda e: emitted.append(e))
    with eng._lock:
        eng._positions["NAORISUSDT"] = {
            "symbol": "NAORISUSDT",
            "base_size_i": 32_330_000_000,
            "quote_usdt_micros": 10_000_000,
            "deal_funds_micros": 10_000_000,
            "entry_price_i": 3_000_000,
            "sector": "spot_alts",
            "dry_run": False,
        }
        eng._positions["XLMUSDT"] = {
            "symbol": "XLMUSDT",
            "base_size_i": 11_547_000_000,
            "quote_usdt_micros": 15_000_000,
            "entry_price_i": 12_000_000,
            "sector": "spot_alts",
            "dry_run": False,
        }
        eng._save_positions_locked()

    class _FakeTrader:
        ready = True

        def get_available_base_i(self, ccy: str) -> int:
            c = str(ccy).upper()
            if c == "NAORIS":
                return 0
            if c == "XLM":
                return 11_547_000_000
            return 0

    eng._trader = _FakeTrader()  # type: ignore[assignment]
    out = eng.drop_externally_closed()
    assert out["ok"] is True
    assert "NAORISUSDT" in out["dropped"]
    assert "XLMUSDT" in out["kept"]
    assert "NAORISUSDT" not in eng._positions
    assert "XLMUSDT" in eng._positions
    assert any(e.get("source") == "external_sync" for e in emitted)
