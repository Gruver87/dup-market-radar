"""Trade event audit JSONL + auto on_action hook."""

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.events import record_trade_event
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_record_trade_event(tmp_path, monkeypatch):
    import radar.trade.events as ev

    path = tmp_path / "trade_events.jsonl"
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    record_trade_event(
        {
            "source": "manual",
            "side": "buy",
            "symbol": "BTCUSDT",
            "ok": True,
            "dry_run": True,
            "message": "preview",
            "quote_usdt_micros": 10_000_000,
        }
    )
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip()
    assert "BTCUSDT" in line
    assert "quote_usdt_micros" in line


def test_auto_on_action_emits(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    seen = []

    eng = AutoTradeEngine(
        KuCoinManualTrader(enforce_symbol_meta=False),
        lambda: {
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "SOLUSDT",
                        "setup": "fade-watch",
                        "score_centi": 6000,
                        "price_i": 90_000_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            }
        },
        on_action=lambda e: seen.append(e),
    )
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000,
            "quote_usdt_micros": 10_000_000,
        }
    }
    monkeypatch.setattr(
        eng,
        "_exec_sell",
        lambda *a, **k: {
            "ok": True,
            "dry_run": True,
            "side": "sell",
            "symbol": "SOLUSDT",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "fill_reconcile": False,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "sell_setups": ["fade-watch"],
                "min_score_centi": 5000,
                "quote_usdt_micros_per_buy": 10_000_000,
                "max_open_positions": 5,
                "max_buys_per_cycle": 0,
                "max_sells_per_cycle": 5,
                "stop_loss_bps": 1200,
                "require_no_risk_flags": True,
                "positions_backup_keep": 2,
            }
        },
    )
    eng.run_once()
    assert any(e.get("side") == "sell" and e.get("source") == "auto" for e in seen)
