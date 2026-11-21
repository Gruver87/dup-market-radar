"""Equal-sleeve sizing: split free USDT across sectors; P&L stays in-sector."""

from __future__ import annotations

from radar.money import QUOTE_SCALE
from radar.trade.auto_engine import (
    AutoTradeEngine,
    split_equal_micros,
    usdt_base_i_to_micros,
)
from radar.trade.kucoin_manual import KuCoinManualTrader


def _patch_paths(ae, tmp_path, monkeypatch):
    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")


def _cfg(**extra):
    base = {
        "dry_run": True,
        "sizing_mode": "equal_sleeves",
        "sleeve_paper_seed_usdt_micros": 90 * QUOTE_SCALE,
        "allowed_sectors": ["spot_alts", "memes", "listings"],
        "buy_setups": ["buy-watch"],
        "sell_setups": ["fade-watch"],
        "max_open_positions": 5,
        "max_buys_per_cycle": 2,
        "max_sells_per_cycle": 5,
        "max_daily_buys": 20,
        "max_daily_notional_usdt_micros": 500 * QUOTE_SCALE,
        "signal_cooldown_sec": 0,
        "fill_reconcile": False,
        "require_no_risk_flags": True,
        "min_score_centi": 5000,
        "stop_loss_bps": 1200,
    }
    base.update(extra)
    return {"auto_trade": base, "native": {"prefer": False}, "api": {"max_order_quote_usdt_micros": 500 * QUOTE_SCALE}}


def _snap(sector: str = "memes", symbol: str = "PEPEUSDT", setup: str = "buy-watch"):
    return {
        "ok": True,
        "mtf_ensure_ok": True,
        "score_backend": "radar_native",
        "sectors": {
            sector: [
                {
                    "symbol": symbol,
                    "setup": setup,
                    "score_centi": 9000,
                    "price_i": 100_000_000_000,
                    "risk_flags": [],
                    "venue": "kucoin",
                    "market": "spot",
                }
            ]
        },
    }


def test_split_equal_micros_remainder_first():
    assert split_equal_micros(100, 3) == (34, 33, 33)
    assert sum(split_equal_micros(90 * QUOTE_SCALE, 3)) == 90 * QUOTE_SCALE


def test_usdt_base_i_to_micros():
    # 12.5 USDT in BASE_SCALE 1e8 → 12_500_000 micros
    assert usdt_base_i_to_micros(1_250_000_000) == 12_500_000


def test_equal_sleeves_buy_uses_third_of_seed(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []

    def fake_buy(sym, quote, price_i, sector, dry):
        buys.append({"sym": sym, "quote": quote, "sector": sector})
        return {
            "ok": True,
            "dry_run": True,
            "side": "buy",
            "symbol": sym,
            "quote_usdt_micros": quote,
            "message": "dry",
        }

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _snap("memes", "PEPEUSDT"))
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
    monkeypatch.setattr(eng, "_exec_buy", fake_buy)
    monkeypatch.setattr(ae, "load_config", lambda: _cfg())
    summary = eng.run_once()
    assert any(a.get("ok") and a.get("side") == "buy" for a in summary["actions"])
    assert buys and buys[0]["quote"] == 30 * QUOTE_SCALE
    assert buys[0]["sector"] == "memes"
    st = eng.status()
    assert st["sizing_mode"] == "equal_sleeves"
    assert st["sleeves"]["memes"] == 0  # spent full sleeve
    assert st["sleeves"]["spot_alts"] == 30 * QUOTE_SCALE
    assert st["sleeves"]["listings"] == 30 * QUOTE_SCALE


def test_equal_sleeves_sell_credits_same_sector(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: _snap("memes", "PEPEUSDT", setup="fade-watch"),
    )
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
        eng._state["sleeves"] = {
            "spot_alts": 30 * QUOTE_SCALE,
            "memes": 0,
            "listings": 30 * QUOTE_SCALE,
        }
        eng._state["sleeves_seed_usdt_micros"] = 90 * QUOTE_SCALE
        eng._positions["PEPEUSDT"] = {
            "symbol": "PEPEUSDT",
            "entry_price_i": 100_000_000_000,
            "quote_usdt_micros": 30 * QUOTE_SCALE,
            # size ≈ quote * PRICE_SCALE / price → 30 USDT at price_i=1e11
            "base_size_i": 30_000,
            "sector": "memes",
            "size_basis": "estimated_fee_adjusted",
        }
        eng._save_positions_locked()
        eng._save_state()

    monkeypatch.setattr(
        eng,
        "_exec_sell",
        lambda *a, **k: {
            "ok": True,
            "dry_run": True,
            "side": "sell",
            "symbol": "PEPEUSDT",
            "message": "dry",
            # Simulate profitable exit: 45 USDT back to memes sleeve
            "deal_funds_micros": 45 * QUOTE_SCALE,
        },
    )
    monkeypatch.setattr(ae, "load_config", lambda: _cfg())
    summary = eng.run_once()
    assert any(a.get("ok") and a.get("side") == "sell" for a in summary["actions"])
    assert "PEPEUSDT" not in eng._positions
    # Profit stays in memes: 0 + 45 USDT; other sleeves untouched
    assert eng.status()["sleeves"]["memes"] == 45 * QUOTE_SCALE
    assert eng.status()["sleeves"]["spot_alts"] == 30 * QUOTE_SCALE


def test_equal_sleeves_blocks_second_open_in_same_sector(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _snap("memes", "WIFUSDT"))
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
        eng._state["sleeves"] = {
            "spot_alts": 30 * QUOTE_SCALE,
            "memes": 30 * QUOTE_SCALE,
            "listings": 30 * QUOTE_SCALE,
        }
        eng._positions["PEPEUSDT"] = {
            "symbol": "PEPEUSDT",
            "sector": "memes",
            "entry_price_i": 1,
            "quote_usdt_micros": 10,
            "base_size_i": 1,
        }
        eng._save_positions_locked()
        eng._save_state()
    monkeypatch.setattr(
        eng,
        "_exec_buy",
        lambda *a, **k: buys.append(1)
        or {"ok": True, "dry_run": True, "side": "buy", "symbol": a[0]},
    )
    monkeypatch.setattr(ae, "load_config", lambda: _cfg())
    summary = eng.run_once()
    skipped = [a for a in summary["actions"] if a.get("skipped")]
    assert skipped
    assert "already has open" in str(skipped[0].get("message") or "")
    assert buys == []
