""" /api/snapshot soft rate-limit (non-force). """

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.rate_limit import RateLimiter


def test_snapshot_rate_soft_refuse(monkeypatch):
    monkeypatch.setattr(
        app_mod,
        "_SNAPSHOT_LIMITER",
        RateLimiter(max_per_window=1, window_ms=60_000),
    )
    monkeypatch.setattr(
        app_mod,
        "_get_snapshot",
        lambda force=False: {
            "ok": True,
            "generated_at_ms": 1,
            "score_backend": "python",
            "universe_size": 0,
            "sectors": {},
        },
    )
    monkeypatch.setattr(app_mod._trader, "status", lambda: {})
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {"positions_sha256": "", "positions": {}},
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    monkeypatch.setattr(app_mod._cache, "status", lambda: {"stats": {}})

    client = TestClient(app_mod.app)
    r1 = client.get("/api/snapshot")
    assert r1.status_code == 200
    r2 = client.get("/api/snapshot")
    assert r2.status_code == 429
    assert "snapshot" in str(r2.json().get("detail") or "").lower()
    assert "Retry-After" in r2.headers
