from radar.metrics import PrometheusMetricsExporter, to_prometheus
from radar.observability.ports import MetricsSnapshot


def test_exporter_fail_closed_nan():
    exp = PrometheusMetricsExporter()
    snap = MetricsSnapshot(
        last_scan_ok=True,
        universe_size=10,
        cache={"stats": {"hits": float("nan")}},
        recovery={"running": True, "consecutive_scan_fails": 0, "stats": {}},
    )
    text = exp.render(snap)
    assert "radar_up 1" in text
    assert "radar_last_scan_ok 1" in text
    assert "radar_recovery_running 1" in text
    # NaN must not appear as a gauge value
    assert "nan" not in text.lower()


def test_to_prometheus_payload_compat():
    body = to_prometheus(
        {
            "last_scan_ok": True,
            "universe_size": 3,
            "native_available": True,
            "cache": {},
            "circuit": {},
            "recovery": {"running": False, "stats": {"network_recoveries": 2}},
        },
        deployment_mode="lab",
    )
    assert "radar_up 1" in body
    assert "radar_recovery_network_recoveries_total 2" in body
