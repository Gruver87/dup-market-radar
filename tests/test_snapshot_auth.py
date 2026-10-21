from fastapi.testclient import TestClient


def test_snapshot_ok_on_loopback_without_key(monkeypatch):
    import radar.api.app as app_mod

    monkeypatch.setenv("RADAR_API_KEY", "")
    monkeypatch.setenv("RADAR_REQUIRE_API_KEY", "0")
    monkeypatch.setattr(
        app_mod,
        "_get_snapshot",
        lambda force=False: {
            "ok": True,
            "schema_version": 2,
            "sectors": {},
            "generated_at_ms": 1,
            "score_backend": "python",
        },
    )
    monkeypatch.setattr(app_mod._cache, "status", lambda: {"stats": {}})
    monkeypatch.setattr(
        app_mod._auto, "status", lambda: {"positions": {}, "positions_sha256": ""}
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    monkeypatch.setattr(app_mod._trader, "status", lambda: {})
    client = TestClient(app_mod.app)
    r = client.get("/api/snapshot")
    assert r.status_code in {200, 304}


def test_snapshot_requires_key_when_forced(monkeypatch):
    import radar.api.app as app_mod

    monkeypatch.setenv("RADAR_API_KEY", "secret-test-key")
    monkeypatch.setenv("RADAR_REQUIRE_API_KEY", "0")
    monkeypatch.setattr(app_mod, "api_key_required", lambda: True)
    monkeypatch.setattr(app_mod, "api_key_gate_ok", lambda: True)
    monkeypatch.setattr(app_mod, "verify_api_key", lambda k: k == "secret-test-key")
    monkeypatch.setattr(
        app_mod,
        "_api_cfg",
        lambda: {
            "snapshot_require_api_key": True,
            "snapshot_require_api_key_non_loopback": True,
            "snapshot_rate_limit_per_min": 10_000,
            "force_scan_rate_limit_per_min": 10_000,
        },
    )
    monkeypatch.setattr(
        app_mod,
        "_get_snapshot",
        lambda force=False: {
            "ok": True,
            "schema_version": 2,
            "sectors": {},
            "generated_at_ms": 1,
        },
    )
    monkeypatch.setattr(app_mod._cache, "status", lambda: {"stats": {}})
    monkeypatch.setattr(
        app_mod._auto, "status", lambda: {"positions": {}, "positions_sha256": ""}
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    monkeypatch.setattr(app_mod._trader, "status", lambda: {})
    client = TestClient(app_mod.app)
    r = client.get("/api/snapshot")
    assert r.status_code == 401
    r2 = client.get("/api/snapshot", headers={"X-Radar-Api-Key": "secret-test-key"})
    assert r2.status_code in {200, 304}
