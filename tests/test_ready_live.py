from fastapi.testclient import TestClient

from radar.api import app as app_mod


def test_live_always_ok():
    client = TestClient(app_mod.app)
    res = client.get("/api/live")
    assert res.status_code == 200
    assert res.json()["live"] is True
    assert "X-Request-Id" in res.headers


def test_ready_503_without_snapshot(monkeypatch):
    # Ensure empty cache peek
    monkeypatch.setattr(app_mod._cache, "peek", lambda: None)
    client = TestClient(app_mod.app)
    res = client.get("/api/ready")
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["ready"] is False
    assert "no_snapshot_yet" in detail["problems"]


def test_ready_503_oid_ledger_degraded(monkeypatch):
    monkeypatch.setattr(
        app_mod._cache, "peek", lambda: {"ok": True, "schema_version": 2}
    )
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {
            "persistence_ok": True,
            "positions_integrity": "ok",
            "enabled": False,
        },
    )
    monkeypatch.setattr(
        app_mod,
        "native_status",
        lambda: {"available": True},
    )
    monkeypatch.setattr(
        "radar.api.app.load_config",
        lambda: {"native": {"prefer": False}},
    )
    monkeypatch.setattr(
        app_mod._trader,
        "status",
        lambda: {
            "oid_ledger": {
                "persist_ok": False,
                "integrity": "corrupt",
            }
        },
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    client = TestClient(app_mod.app)
    res = client.get("/api/ready")
    assert res.status_code == 503
    assert "oid_ledger_persist_degraded" in res.json()["detail"]["problems"]


def test_health_flags_tg_watch_persist(monkeypatch):
    monkeypatch.setattr(
        app_mod._cache, "peek", lambda: {"ok": True, "schema_version": 2}
    )
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {
            "persistence_ok": True,
            "positions_integrity": "ok",
            "enabled": False,
        },
    )
    monkeypatch.setattr(app_mod, "native_status", lambda: {"available": True})
    monkeypatch.setattr(
        "radar.api.app.load_config",
        lambda: {"native": {"prefer": False}, "ingest": {}},
    )
    monkeypatch.setattr(
        app_mod._trader,
        "status",
        lambda: {"oid_ledger": {"persist_ok": True}, "base_url_ok": True},
    )

    class FakeTg:
        def status(self):
            return {
                "running": True,
                "env_ready": True,
                "watches": {"persist_ok": False, "count": 0},
            }

    monkeypatch.setattr(app_mod, "_tg", FakeTg())
    client = TestClient(app_mod.app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "telegram_watches_persist_degraded" in body["problems"]


def test_ready_503_kucoin_base_url_refused(monkeypatch):
    monkeypatch.setattr(
        app_mod._cache, "peek", lambda: {"ok": True, "schema_version": 2}
    )
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {
            "persistence_ok": True,
            "positions_integrity": "ok",
            "enabled": False,
        },
    )
    monkeypatch.setattr(app_mod, "native_status", lambda: {"available": True})
    monkeypatch.setattr(
        "radar.api.app.load_config",
        lambda: {"native": {"prefer": False}},
    )
    monkeypatch.setattr(
        app_mod._trader,
        "status",
        lambda: {
            "oid_ledger": {"persist_ok": True},
            "base_url_ok": False,
            "symbol_meta": {"base_url_ok": True},
        },
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    client = TestClient(app_mod.app)
    res = client.get("/api/ready")
    assert res.status_code == 503
    assert "kucoin_base_url_refused" in res.json()["detail"]["problems"]


def test_ready_503_symbol_meta_base_refused(monkeypatch):
    monkeypatch.setattr(
        app_mod._cache, "peek", lambda: {"ok": True, "schema_version": 2}
    )
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {
            "persistence_ok": True,
            "positions_integrity": "ok",
            "enabled": False,
        },
    )
    monkeypatch.setattr(app_mod, "native_status", lambda: {"available": True})
    monkeypatch.setattr(
        "radar.api.app.load_config",
        lambda: {"native": {"prefer": False}},
    )
    monkeypatch.setattr(
        app_mod._trader,
        "status",
        lambda: {
            "oid_ledger": {"persist_ok": True},
            "base_url_ok": True,
            "symbol_meta": {"base_url_ok": False},
        },
    )
    monkeypatch.setattr(app_mod, "_tg", None)
    client = TestClient(app_mod.app)
    res = client.get("/api/ready")
    assert res.status_code == 503
    assert "symbol_meta_base_url_refused" in res.json()["detail"]["problems"]
