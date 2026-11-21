from radar.trade.auto_engine import AutoTradeEngine, _MIN_SHARED_BUY_MICROS
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_shared_pool_quote_fair_share(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    monkeypatch.setattr(
        eng,
        "_read_free_usdt_micros",
        lambda cfg: (60_000_000, None),  # 60 USDT
    )
    cfg = {
        "sizing_mode": "shared_pool",
        "quote_usdt_micros_per_buy": 25_000_000,
        "shared_pool_reserve_usdt_micros": 0,
        "dry_run": True,
    }
    q, err = eng._shared_pool_buy_quote(cfg, open_n=2, max_open=4)
    assert err is None
    # usable 60 / 2 slots left = 30, capped by 25 → 25
    assert q == 25_000_000


def test_shared_pool_skips_sector_lock(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    buys = []

    def fake_buy(sym, quote, price_i, sector, dry):
        buys.append((sym, quote, sector))
        return {
            "ok": True,
            "dry_run": True,
            "side": "buy",
            "symbol": sym,
            "quote_usdt_micros": quote,
            "message": "dry",
        }

    snap = {
        "ok": True,
        "mtf_ensure_ok": True,
        "score_backend": "radar_native",
        "sectors": {
            "spot_alts": [
                {
                    "symbol": "AAAUSDT",
                    "setup": "buy-watch",
                    "score_centi": 9000,
                    "price_i": 100_000_000_000,
                    "risk_flags": [],
                    "venue": "kucoin",
                    "market": "spot",
                }
            ],
            "memes": [
                {
                    "symbol": "BBBUSDT",
                    "setup": "buy-watch",
                    "score_centi": 8800,
                    "price_i": 100_000_000_000,
                    "risk_flags": [],
                    "venue": "kucoin",
                    "market": "spot",
                }
            ],
        },
    }
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: snap)
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
        eng._positions["OLDUSDT"] = {
            "symbol": "OLDUSDT",
            "sector": "spot_alts",
            "base_size_i": 1_000_000_000,
            "quote_usdt_micros": 10_000_000,
            "entry_price_i": 1_000_000,
        }
    monkeypatch.setattr(eng, "_exec_buy", fake_buy)
    monkeypatch.setattr(
        eng,
        "_read_free_usdt_micros",
        lambda cfg: (100_000_000, None),
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "sizing_mode": "shared_pool",
                "allowed_sectors": ["spot_alts", "memes"],
                "buy_setups": ["buy-watch"],
                "max_open_positions": 4,
                "max_buys_per_cycle": 2,
                "max_daily_buys": 20,
                "max_daily_notional_usdt_micros": 500_000_000,
                "signal_cooldown_sec": 0,
                "quote_usdt_micros_per_buy": 15_000_000,
                "shared_pool_reserve_usdt_micros": 0,
                "fill_reconcile": False,
                "require_no_risk_flags": True,
                "exec_venues": ["kucoin"],
            },
            "native": {"prefer": False},
            "api": {"max_order_quote_usdt_micros": 500_000_000},
        },
    )
    summary = eng.run_once()
    assert summary["ok"] is True
    # Must buy despite spot_alts already having OLDUSDT (no sleeve lock).
    assert any(b[0] == "AAAUSDT" for b in buys)
    assert all(b[1] >= _MIN_SHARED_BUY_MICROS for b in buys)
