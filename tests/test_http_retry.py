from unittest.mock import MagicMock, patch

import httpx
import pytest

from radar.http_client import AllowlistHttp


def test_retries_on_429_then_ok():
    http = AllowlistHttp({"api.example.com"}, timeout_ms=2000, retries=2, backoff_ms=1)
    good = MagicMock()
    good.status_code = 200
    good.content = b'{"ok": true}'
    good.headers = {}
    good.raise_for_status = MagicMock()

    bad = MagicMock()
    bad.status_code = 429
    bad.text = "rate"
    bad.request = MagicMock()
    bad.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("429", request=bad.request, response=bad)
    )

    with patch.object(http._client, "get", side_effect=[bad, good]):
        body = http.get_json("https://api.example.com/x", use_cache=False)
    assert body == {"ok": True}
    assert http.stats["retries"] >= 1
    assert http.stats["rate_limited"] >= 1
    http.close()


def test_no_retry_on_400():
    """Client errors must not burn retries (kline 400 spam)."""
    http = AllowlistHttp({"api.example.com"}, timeout_ms=2000, retries=3, backoff_ms=1)
    bad = MagicMock()
    bad.status_code = 400
    bad.text = "bad symbol"
    bad.request = MagicMock()
    bad.headers = {}
    bad.content = b"bad"
    bad.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "400 Bad Request", request=bad.request, response=bad
        )
    )
    with patch.object(http._client, "get", return_value=bad) as get:
        with pytest.raises(httpx.HTTPStatusError):
            http.get_json("https://api.example.com/klines", use_cache=False)
    assert get.call_count == 1
    assert http.stats["retries"] == 0
    http.close()


def test_allowlist_still_refuses():
    http = AllowlistHttp({"api.binance.com"}, timeout_ms=1000, retries=0)
    with pytest.raises(PermissionError):
        http.get_json("https://evil.example/x")
    http.close()
