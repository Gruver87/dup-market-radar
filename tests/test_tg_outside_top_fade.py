from radar.telegram.alerts import evaluate_watch


def test_outside_top_fade_take_profit():
    watch = {
        "symbol": "OUTUSDT",
        "market": "spot",
        "chat_id": 1,
        "entry_price_i": 100_000_000_000,
        "last_setup": "fade-watch",
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    alert = evaluate_watch(
        watch,
        {"sectors": {}},
        stop_loss_bps=1200,
        take_profit_warn_bps=800,
        mark_price_fn=lambda s: 110_000_000_000,  # +10%
        outside_top_fade_alerts=True,
    )
    assert alert is not None
    assert alert["severity"] == "warn"
    assert alert["setup"] == "outside_top_fade"
    assert any("outside_top_fade" in r for r in alert["reasons"])


def test_outside_top_fade_disabled():
    watch = {
        "symbol": "OUTUSDT",
        "market": "spot",
        "chat_id": 1,
        "entry_price_i": 100_000_000_000,
        "last_setup": "fade-watch",
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    alert = evaluate_watch(
        watch,
        {"sectors": {}},
        take_profit_warn_bps=800,
        mark_price_fn=lambda s: 110_000_000_000,
        outside_top_fade_alerts=False,
    )
    assert alert is None


def test_outside_top_fade_needs_last_setup():
    watch = {
        "symbol": "OUTUSDT",
        "market": "spot",
        "chat_id": 1,
        "entry_price_i": 100_000_000_000,
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    alert = evaluate_watch(
        watch,
        {"sectors": {}},
        take_profit_warn_bps=800,
        mark_price_fn=lambda s: 110_000_000_000,
        outside_top_fade_alerts=True,
    )
    assert alert is None
