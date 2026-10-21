"""Public JSON must not leak absolute filesystem paths."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod


def _assert_no_abs_paths(obj, *, _key: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"path", "ledger_path", "data_dir"}:
                assert v == "<redacted>", f"{_key}.{k} leaked: {v!r}"
            else:
                _assert_no_abs_paths(v, _key=f"{_key}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_abs_paths(v, _key=f"{_key}[{i}]")


def test_health_redacts_persist_paths():
    client = TestClient(app_mod.app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    _assert_no_abs_paths(body)
    oid = (body.get("manual_trade") or {}).get("oid_ledger") or {}
    if "path" in oid:
        assert oid["path"] == "<redacted>"


def test_trade_status_redacts_persist_paths(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    client = TestClient(app_mod.app)
    res = client.get("/api/trade/status")
    assert res.status_code == 200
    _assert_no_abs_paths(res.json())


def test_snapshot_redacts_position_book(monkeypatch):
    client = TestClient(app_mod.app)
    monkeypatch.setattr(
        app_mod,
        "_get_snapshot",
        lambda force=False: {
            "ok": True,
            "generated_at": 1,
            "generated_at_ms": 1000,
            "score_backend": "test",
            "universe_size": 0,
            "sectors": {},
            "cache": {},
        },
    )
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {
            "enabled": False,
            "open_positions": 1,
            "positions": {"BTCUSDT": {"base_size_i": 1, "order_id": "oid-secret"}},
            "positions_integrity": "ok",
            "persistence_ok": True,
        },
    )
    res = client.get("/api/snapshot")
    assert res.status_code == 200
    at = res.json().get("auto_trade") or {}
    assert at.get("positions") == {}
    assert at.get("positions_redacted") is True
    assert at.get("open_positions") == 1


def test_snapshot_redacts_persist_paths(monkeypatch):
    client = TestClient(app_mod.app)
    monkeypatch.setattr(
        app_mod,
        "_get_snapshot",
        lambda force=False: {
            "ok": True,
            "generated_at": 1,
            "generated_at_ms": 1000,
            "score_backend": "test",
            "universe_size": 0,
            "sectors": {},
            "cache": {},
        },
    )
    res = client.get("/api/snapshot")
    assert res.status_code == 200
    _assert_no_abs_paths(res.json())
