import threading
import time

from radar.scan_cache import SnapshotCache


def test_cache_hit_within_refresh():
    cache = SnapshotCache()
    calls = {"n": 0}

    def scan():
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}

    a = cache.get(force=False, refresh_ms=60_000, scan_fn=scan)
    b = cache.get(force=False, refresh_ms=60_000, scan_fn=scan)
    assert a["n"] == 1 and b["n"] == 1
    assert calls["n"] == 1
    assert cache.stats["hits"] >= 1


def test_single_flight_one_scan():
    cache = SnapshotCache()
    calls = {"n": 0}
    barrier = threading.Barrier(3)

    def scan():
        calls["n"] += 1
        time.sleep(0.15)
        return {"ok": True, "n": calls["n"]}

    results = []

    def worker():
        barrier.wait()
        results.append(cache.get(force=True, refresh_ms=1, scan_fn=scan))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert calls["n"] == 1
    assert all(r["n"] == 1 for r in results)
    assert cache.stats["single_flight_waits"] >= 1


def test_scan_error_counted_and_clears_inflight():
    cache = SnapshotCache()

    def boom():
        raise RuntimeError("scan boom")

    try:
        cache.get(force=True, refresh_ms=1, scan_fn=boom)
        assert False, "expected raise"
    except RuntimeError as exc:
        assert "scan boom" in str(exc)
    assert cache.stats["scan_errors"] == 1
    assert cache.status()["inflight"] is False
