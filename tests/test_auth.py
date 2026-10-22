
from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.auth import (
    api_key_gate_ok,
    api_key_required,
    radar_api_key,
    require_api_key_env,
    verify_api_key,
)
from radar.auth_stats import AUTH_STATS
from radar.secret_mgmt import reset_secret_manager_for_tests


def test_gate_off_when_unset(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_SECRET_BACKEND", raising=False)
    monkeypatch.delenv("RADAR_DEPLOYMENT_MODE", raising=False)
    assert radar_api_key() is None
    assert api_key_required() is False
    assert require_api_key_env() is False
    assert api_key_gate_ok() is True
    assert verify_api_key(None) is True
    assert verify_api_key("anything") is True


def test_require_api_key_without_key_refuses_trade(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    monkeypatch.setenv("RADAR_REQUIRE_API_KEY", "1")
    assert api_key_gate_ok() is False
    before = AUTH_STATS.snapshot()["auth_fail"]
    client = TestClient(app_mod.app)
    res = client.post("/api/trade/auto", json={"enabled": False})
    assert res.status_code == 503
    assert "RADAR_API_KEY missing" in res.json()["detail"]
    assert AUTH_STATS.snapshot()["auth_fail"] == before + 1


def test_gate_on_compare_digest(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.setenv("RADAR_API_KEY", "secret-lab-key")
    assert api_key_required() is True
    assert verify_api_key("secret-lab-key") is True
    assert verify_api_key("wrong") is False
    assert verify_api_key(None) is False


def test_api_key_length_bounds(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.setenv("RADAR_API_KEY", "short")
    assert radar_api_key() is None
    monkeypatch.setenv("RADAR_API_KEY", "good-lab-key-ok")
    assert radar_api_key() == "good-lab-key-ok"
    assert verify_api_key("x" * 300) is False
    assert verify_api_key("short") is False


def test_env_native_override(monkeypatch):
    from radar.config import apply_env_overrides

    monkeypatch.setenv("RADAR_NATIVE_PREFER", "0")
    monkeypatch.setenv("RADAR_NATIVE_FAIL_CLOSED", "0")
    cfg = apply_env_overrides({"native": {"prefer": True, "fail_closed": True}})
    assert cfg["native"]["prefer"] is False
    assert cfg["native"]["fail_closed"] is False


def test_trade_auth_fail_increments_counter(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.setenv("RADAR_API_KEY", "secret-lab-key-zzzz")
    before = AUTH_STATS.snapshot()["auth_fail"]
    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/auto",
        json={"enabled": False},
        headers={"X-Radar-Api-Key": "wrong-key"},
    )
    assert res.status_code == 401
    assert AUTH_STATS.snapshot()["auth_fail"] == before + 1


def test_trade_auto_get_requires_api_key(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.setenv("RADAR_API_KEY", "secret-lab-key-get")
    client = TestClient(app_mod.app)
    deny = client.get("/api/trade/auto")
    assert deny.status_code == 401
    ok = client.get(
        "/api/trade/auto",
        headers={"X-Radar-Api-Key": "secret-lab-key-get"},
    )
    assert ok.status_code == 200
    assert ok.json().get("ok") is True
    assert "positions" in ok.json()
