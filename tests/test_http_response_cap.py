"""Ingest HTTP response size fail-closed cap."""

import httpx
import pytest

from radar.http_client import (
    SIGNED_MAX_RESPONSE_BYTES,
    AllowlistHttp,
    read_capped_json,
)


class _FakeResp:
    def __init__(self, *, status: int, content: bytes, headers: dict | None = None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", errors="replace")
        self.request = httpx.Request("GET", "https://api.example.com/x")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=self.request,
                response=self,  # type: ignore[arg-type]
            )


def test_content_length_cap_refuses(monkeypatch):
    http = AllowlistHttp({"api.example.com"}, max_response_bytes=2048, retries=0)

    def fake_get(url, params=None):
        return _FakeResp(
            status=200,
            content=b'{"ok":true}',
            headers={"content-length": "9000"},
        )

    monkeypatch.setattr(http._client, "get", fake_get)
    try:
        http.get_json("https://api.example.com/x", use_cache=False)
        assert False, "expected refuse"
    except RuntimeError as exc:
        assert "too large" in str(exc).lower()
    assert http.stats["response_too_large"] == 1


def test_body_bytes_cap_refuses(monkeypatch):
    http = AllowlistHttp({"api.example.com"}, max_response_bytes=2048, retries=0)
    big = b'{"pad":"' + (b"x" * 3000) + b'"}'

    def fake_get(url, params=None):
        return _FakeResp(status=200, content=big, headers={})

    monkeypatch.setattr(http._client, "get", fake_get)
    try:
        http.get_json("https://api.example.com/x", use_cache=False)
        assert False, "expected refuse"
    except RuntimeError as exc:
        assert "too large" in str(exc).lower()


def test_read_capped_json_signed_budget():
    ok = _FakeResp(status=200, content=b'{"code":"200000"}', headers={})
    assert read_capped_json(ok, SIGNED_MAX_RESPONSE_BYTES, label="t")["code"] == "200000"
    big = _FakeResp(
        status=200,
        content=b"x" * (SIGNED_MAX_RESPONSE_BYTES + 1),
        headers={},
    )
    with pytest.raises(RuntimeError, match="too large"):
        read_capped_json(big, SIGNED_MAX_RESPONSE_BYTES, label="kucoin-signed")
