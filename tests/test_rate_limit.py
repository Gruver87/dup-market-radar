from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.rate_limit import MAX_KEY_LEN, RateLimiter


def test_allows_under_cap_then_refuses():
    rl = RateLimiter(max_per_window=3, window_ms=60_000)
    assert rl.allow("127.0.0.1") is True
    assert rl.allow("127.0.0.1") is True
    assert rl.allow("127.0.0.1") is True
    assert rl.allow("127.0.0.1") is False
    assert rl.stats["refused"] == 1
    # other key independent
    assert rl.allow("10.0.0.2") is True


def test_normalize_key_truncates():
    long = "x" * 200
    assert len(RateLimiter.normalize_key(long)) == MAX_KEY_LEN
    assert RateLimiter.normalize_key("  ") == "unknown"
    assert RateLimiter.normalize_key("a\x00b") == "ab"


def test_max_keys_soft_refuse():
    rl = RateLimiter(max_per_window=10, window_ms=60_000, max_keys=16)
    for i in range(16):
        assert rl.allow(f"k{i}") is True
    assert rl.allow("k-overflow") is False
    assert rl.stats["key_cap_refused"] == 1
    # Existing key still allowed
    assert rl.allow("k0") is True


def test_check_returns_retry_after():
    rl = RateLimiter(max_per_window=1, window_ms=5_000)
    ok, ra = rl.check("k")
    assert ok is True and ra == 0
    ok2, ra2 = rl.check("k")
    assert ok2 is False
    assert ra2 >= 1


def test_window_expiry(monkeypatch):
    rl = RateLimiter(max_per_window=1, window_ms=100)
    t = {"now": 1_000_000}

    def fake_time():
        return t["now"] / 1000.0

    monkeypatch.setattr("radar.rate_limit.time.time", fake_time)
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    t["now"] += 200
    assert rl.allow("a") is True


def test_trade_429_has_retry_after(monkeypatch):
    from radar.rate_limit import RateLimiter

    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_TRUST_PROXY", raising=False)
    tight = RateLimiter(max_per_window=1, window_ms=60_000)
    monkeypatch.setattr(app_mod, "_TRADE_LIMITER", tight)
    client = TestClient(app_mod.app)
    body = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "confirm": False,
        "quote_usdt_micros": 1_000_000,
    }
    assert client.post("/api/trade/order", json=body).status_code == 200
    res = client.post("/api/trade/order", json=body)
    assert res.status_code == 429
    assert "Retry-After" in res.headers
    assert int(res.headers["Retry-After"]) >= 1


def test_xff_ignored_without_trust_proxy(monkeypatch):
    """Spoofed X-Forwarded-For must not create a separate rate-limit bucket."""
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_TRUST_PROXY", raising=False)
    tight = RateLimiter(max_per_window=1, window_ms=60_000)
    monkeypatch.setattr(app_mod, "_TRADE_LIMITER", tight)
    client = TestClient(app_mod.app)
    body = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "confirm": False,
        "quote_usdt_micros": 1_000_000,
    }
    assert client.post("/api/trade/order", json=body).status_code == 200
    res = client.post(
        "/api/trade/order",
        json=body,
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert res.status_code == 429


def test_xff_honored_when_trust_proxy(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.setenv("RADAR_TRUST_PROXY", "1")
    tight = RateLimiter(max_per_window=1, window_ms=60_000)
    monkeypatch.setattr(app_mod, "_TRADE_LIMITER", tight)
    client = TestClient(app_mod.app)
    body = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "confirm": False,
        "quote_usdt_micros": 1_000_000,
    }
    assert client.post("/api/trade/order", json=body).status_code == 200
    # Different XFF → separate bucket → allowed
    res = client.post(
        "/api/trade/order",
        json=body,
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert res.status_code == 200
