"""Secondary venue soft-wall timeout (Dex/CoinGecko) — 0.2.81."""

from __future__ import annotations

import httpx
import pytest

from radar.http_client import AllowlistHttp, _SECONDARY_HOSTS


class _FakeResp:
    def __init__(self) -> None:
        self.status_code = 200
        self.request = httpx.Request("GET", "https://api.dexscreener.com/x")
        self.text = "{}"

    def raise_for_status(self) -> None:
        return None


def test_secondary_hosts_include_dex_and_coingecko():
    assert "api.dexscreener.com" in _SECONDARY_HOSTS
    assert "api.coingecko.com" in _SECONDARY_HOSTS


def test_secondary_timeout_one_shot_no_retry(monkeypatch):
    http = AllowlistHttp(
        {"api.dexscreener.com"},
        timeout_ms=12_000,
        retries=2,
        secondary_timeout_ms=2500,
    )
    calls: list[object] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(timeout)
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(http._client, "get", fake_get)
    with pytest.raises(httpx.TimeoutException):
        http.get_json("https://api.dexscreener.com/token-boosts/top/v1", use_cache=False)
    assert len(calls) == 1
    assert isinstance(calls[0], httpx.Timeout)
    http.close()


def test_cex_keeps_default_timeout_and_retries(monkeypatch):
    http = AllowlistHttp(
        {"api.kucoin.com"},
        timeout_ms=12_000,
        retries=1,
        secondary_timeout_ms=2500,
    )
    calls: list[object] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(timeout)
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(http._client, "get", fake_get)
    with pytest.raises(httpx.TimeoutException):
        http.get_json("https://api.kucoin.com/api/v1/market/allTickers", use_cache=False)
    # retries=1 → attempts=2; default client timeout (None kw) used
    assert len(calls) == 2
    assert calls[0] is None
    http.close()
