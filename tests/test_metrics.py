from radar.metrics import _prom_label_value, build_metrics_payload, to_prometheus


def test_prometheus_contains_core_gauges():
    payload = build_metrics_payload(
        cache={
            "inflight": False,
            "age_ms": 12,
            "stats": {"hits": 3, "misses": 1, "scans": 1, "scan_errors": 2},
        },
        circuit={"open_remaining_ms": {"mexc": 5000}},
        snap={
            "ok": True,
            "schema_version": 2,
            "universe_size": 42,
            "elapsed_ms": 900,
            "score_backend": "radar_native",
            "http_stats": {"gets": 10, "retries": 1, "rate_limited": 0, "cache_hits": 2},
            "dedupe": {"spot": {"dropped": 5}},
        },
        auto={"enabled": False, "open_positions": 0, "persistence_ok": True},
        native={"available": True},
        trade_limiter={"allowed": 7, "refused": 1},
        trade_stats={
            "place_ok": 2,
            "place_fail": 1,
            "duplicate_oid": 1,
            "fills_ok": 2,
            "sell_clamped": 1,
        },
        telegram={
            "running": True,
            "watches": {"count": 4, "persist_ok": True},
            "circuit": {"open_remaining_ms": {}},
            "stats": {
                "alerts_sent": 9,
                "alerts_skipped_stale": 2,
                "alerts_skipped_empty": 1,
                "alerts_skipped_degraded": 4,
                "watch_add_fail": 3,
            },
        },
        auth_stats={"auth_ok": 5, "auth_fail": 2},
    )
    text = to_prometheus(payload)
    assert "radar_up 1" in text
    assert "radar_cache_hits_total 3" in text
    assert "radar_cache_scan_errors_total 2" in text
    assert 'radar_circuit_open_remaining_ms{venue="mexc"} 5000' in text
    assert "radar_last_scan_ok 1" in text
    assert "radar_trade_rate_refused_total 1" in text
    assert "radar_trade_place_ok_total 2" in text
    assert "radar_trade_duplicate_oid_total 1" in text
    assert "radar_trade_sell_clamped_total 1" in text
    assert "radar_telegram_running 1" in text
    assert "radar_telegram_watches 4" in text
    assert "radar_telegram_alerts_sent_total 9" in text
    assert "radar_telegram_alerts_skipped_stale_total 2" in text
    assert "radar_telegram_alerts_skipped_empty_total 1" in text
    assert "radar_telegram_alerts_skipped_degraded_total 4" in text
    assert "radar_telegram_watch_add_fail_total 3" in text
    assert "radar_auth_ok_total 5" in text
    assert "radar_auth_fail_total 2" in text
    assert payload["telegram"]["alerts_sent"] == 9


def test_prom_label_value_sanitizes_and_truncates() -> None:
    assert _prom_label_value('evil"label\n') == "evil_label"
    assert len(_prom_label_value("x" * 200)) == 64
    assert _prom_label_value("") == "unknown"
    assert _prom_label_value(None) == "unknown"  # type: ignore[arg-type]


def test_prometheus_caps_open_venue_series() -> None:
    open_rem = {f"venue{i}": 1000 + i for i in range(40)}
    text = to_prometheus(
        build_metrics_payload(circuit={"open_remaining_ms": open_rem})
    )
    assert "radar_circuit_open_venues 40" in text
    assert text.count("radar_circuit_open_remaining_ms{") == 32
