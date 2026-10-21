from radar.trade.paper_ledger import PaperLedger


def test_paper_buy_sell_pnl(tmp_path):
    led = PaperLedger(tmp_path / "paper.json")
    led.record_event(
        {
            "ok": True,
            "dry_run": True,
            "side": "buy",
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000_00,  # 1.0 * 1e8
            "quote_usdt_micros": 100_000_000,
            "source": "test",
        }
    )
    st = led.status()
    assert st["open_count"] == 1
    led.record_event(
        {
            "ok": True,
            "dry_run": True,
            "side": "sell",
            "symbol": "SOLUSDT",
            "exit_price_i": 110_000_000_000,
            "source": "test",
        }
    )
    st2 = led.status()
    assert st2["open_count"] == 0
    assert st2["realized_pnl_usdt_micros"] > 0


def test_paper_ignores_live(tmp_path):
    led = PaperLedger(tmp_path / "paper2.json")
    led.record_event(
        {
            "ok": True,
            "dry_run": False,
            "side": "buy",
            "symbol": "XUSDT",
            "entry_price_i": 1,
            "base_size_i": 1,
        }
    )
    assert led.status()["open_count"] == 0
