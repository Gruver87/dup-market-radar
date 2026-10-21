"""Crypto bubbles — integer parse + API contract."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.ops import bubbles as bub


def test_row_from_cg_integer_fields():
    row = bub._row_from_cg(
        {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "current_price": "68000.12",
            "market_cap": "1300000000000",
            "total_volume": "25000000000",
            "price_change_percentage_200d_in_currency": "42.5",
            "image": "https://assets.coingecko.com/coins/images/1/large/bitcoin.png",
        },
        1,
    )
    assert row is not None
    assert row["symbol"] == "BTC"
    assert row["rank"] == 1
    assert isinstance(row["price_i"], int) and row["price_i"] > 0
    assert isinstance(row["market_cap_i"], int) and row["market_cap_i"] > 0
    assert row["change_6m_bps"] == 4250  # 42.5%
    assert row["period"] == "200d"
    assert "6m" in row["period_label"]
    assert row["image"] and row["image"].startswith("https://assets.coingecko.com/")


def test_safe_image_url_refuses_junk():
    assert bub._safe_image_url("javascript:alert(1)") is None
    assert bub._safe_image_url("https://evil.example/x.png") is None
    assert bub._safe_image_url("http://assets.coingecko.com/x.png") is None
    assert (
        bub._safe_image_url("https://assets.coingecko.com/coins/images/1.png")
        == "https://assets.coingecko.com/coins/images/1.png"
    )


def test_get_bubbles_uses_cache(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(*, top_n: int):
        calls["n"] += 1
        return [
            {
                "rank": 1,
                "id": "bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "price_i": 1,
                "market_cap_i": 1_000_000,
                "volume_24h_i": 1,
                "change_6m_bps": 100,
                "period": "200d",
                "period_label": "~6m (200d)",
                "image": None,
            }
        ]

    monkeypatch.setattr(bub, "_fetch_markets", fake_fetch)
    bub._cache = None
    bub._cache_at_ms = 0
    a = bub.get_bubbles(top_n=10, cache_ttl_ms=60_000)
    b = bub.get_bubbles(top_n=10, cache_ttl_ms=60_000)
    assert a["ok"] is True and b["ok"] is True
    assert calls["n"] == 1
    assert b["cache_hit"] is True


def test_api_bubbles_mocked(monkeypatch):
    monkeypatch.setattr(
        bub,
        "get_bubbles",
        lambda **kwargs: {
            "ok": True,
            "coins": [],
            "top_n": 0,
            "period_label": "~6m (200d)",
            "source": "coingecko",
        },
    )
    client = TestClient(app_mod.app)
    res = client.get("/api/ops/bubbles")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_api_bubbles_force_shares_force_scan_limiter(monkeypatch):
    from radar.rate_limit import RateLimiter

    monkeypatch.setattr(
        app_mod,
        "_FORCE_SCAN_LIMITER",
        RateLimiter(max_per_window=1, window_ms=60_000),
    )
    monkeypatch.setattr(
        app_mod,
        "_OPS_LIMITER",
        RateLimiter(max_per_window=100, window_ms=60_000),
    )
    monkeypatch.setattr(
        bub,
        "get_bubbles",
        lambda **kwargs: {
            "ok": True,
            "coins": [],
            "top_n": 0,
            "period_label": "~6m (200d)",
            "source": "coingecko",
        },
    )
    client = TestClient(app_mod.app)
    r1 = client.get("/api/ops/bubbles?force=true")
    assert r1.status_code == 200
    r2 = client.get("/api/ops/bubbles?force=true")
    assert r2.status_code == 429
    assert "force scan" in str(r2.json().get("detail") or "").lower()
