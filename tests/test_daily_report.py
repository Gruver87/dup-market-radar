import json
from pathlib import Path

from radar.trade.daily_report import build_daily_report


def test_daily_report_invested_and_pnl(tmp_path: Path):
    p = tmp_path / "trade_events.jsonl"
    # 2026-11-21 buy 25 USDT, sell +3 → day pnl +3
    # 2026-11-22 buy 15, sell -2 → day pnl -2
    rows = [
        {
            "at_ms": 1795283800000,  # ~2026-11-21
            "ok": True,
            "dry_run": False,
            "source": "auto",
            "side": "buy",
            "symbol": "AAAUSDT",
            "quote_usdt_micros": 25_000_000,
            "base_size_i": 1_000_000_00,
            "entry_price_i": 25_0000_0000,
        },
        {
            "at_ms": 1795283900000,
            "ok": True,
            "dry_run": False,
            "source": "auto",
            "side": "sell",
            "symbol": "AAAUSDT",
            "deal_funds_micros": 28_000_000,
            "quote_usdt_micros": 28_000_000,
            "base_size_i": 1_000_000_00,
            "realized_pnl_usdt_micros": 3_000_000,
            "cost_usdt_micros": 25_000_000,
        },
        {
            "at_ms": 1795360000000,  # ~2026-11-22
            "ok": True,
            "dry_run": False,
            "source": "auto",
            "side": "buy",
            "symbol": "BBBUSDT",
            "quote_usdt_micros": 15_000_000,
            "base_size_i": 2_000_000_00,
        },
        {
            "at_ms": 1795360100000,
            "ok": True,
            "dry_run": False,
            "source": "auto",
            "side": "sell",
            "symbol": "BBBUSDT",
            "deal_funds_micros": 13_000_000,
            "base_size_i": 2_000_000_00,
            "realized_pnl_usdt_micros": -2_000_000,
            "cost_usdt_micros": 15_000_000,
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rep = build_daily_report(path=p, days=30)
    assert rep["ok"] is True
    by = {d["day_utc"]: d for d in rep["days"]}
    d21 = by["2026-11-21"]
    assert d21["buy_usdt_micros"] == 25_000_000
    assert d21["realized_pnl_usdt_micros"] == 3_000_000
    d22 = by["2026-11-22"]
    assert d22["buy_usdt_micros"] == 15_000_000
    assert d22["realized_pnl_usdt_micros"] == -2_000_000
    assert rep["total_realized_pnl_usdt_micros"] == 1_000_000
