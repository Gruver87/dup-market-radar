from radar.telegram.alerts import evaluate_watch


def test_outside_top_stop_via_mark():
    watch = {
        "symbol": "OUTUSDT",
        "market": "spot",
        "chat_id": 1,
        "entry_price_i": 100_000_000_000,
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    snap = {"sectors": {"spot_alts": []}}
    alert = evaluate_watch(
        watch,
        snap,
        stop_loss_bps=1200,
        mark_price_fn=lambda s: 80_000_000_000,
    )
    assert alert is not None
    assert alert["severity"] == "critical"
    assert alert["setup"] == "outside_top"
    assert any("outside_top_mark" in r for r in alert["reasons"])


def test_outside_top_no_alert_without_enough_drop():
    watch = {
        "symbol": "OUTUSDT",
        "market": "spot",
        "chat_id": 1,
        "entry_price_i": 100_000_000_000,
    }
    alert = evaluate_watch(
        watch,
        {"sectors": {}},
        stop_loss_bps=1200,
        mark_price_fn=lambda s: 95_000_000_000,
    )
    assert alert is None
