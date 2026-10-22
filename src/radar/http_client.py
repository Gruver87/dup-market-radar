"""Allowlisted HTTPS client — TLS verify on, int ms timeouts, soft retry on 429/5xx."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Set
from urllib.parse import urlparse

import httpx

from radar.secrets_redact import redact_exc, redact_text

logger = logging.getLogger("radar.http")

# Soft cap against venue memory bombs (KuCoin allTickers is large but << 8 MiB).
DEFAULT_MAX_RESPONSE_BYTES = 20_971_520  # 20 MiB (Binance allTickers ~18 MiB)
# Signed trade GETs/POSTs are tiny; symbols meta can approach ingest scale.
SIGNED_MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB
META_MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
DEFAULT_MAX_CACHE_ENTRIES = 256
DEFAULT_MAX_ERROR_LOG = 64


def read_capped_response(
    resp: httpx.Response,
    max_bytes: int,
    *,
    label: str,
) -> bytes:
    """Refuse oversized HTTP bodies (Content-Length and actual bytes)."""
    cl = resp.headers.get("content-length")
    if cl is not None:
        try:
            declared = int(cl)
        except ValueError as exc:
            raise RuntimeError(f"{label}: invalid Content-Length") from exc
        if declared > max_bytes:
            raise RuntimeError(
                f"{label}: response too large "
                f"(content-length={declared} > {max_bytes})"
            )
    raw = resp.content
    if len(raw) > max_bytes:
        raise RuntimeError(
            f"{label}: response too large (bytes={len(raw)} > {max_bytes})"
        )
    return raw


def read_capped_json(
    resp: httpx.Response,
    max_bytes: int,
    *,
    label: str,
) -> Any:
    raw = read_capped_response(resp, max_bytes, label=label)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label}: invalid JSON") from exc


# Soft-wall hosts — memes/stables must not stretch ingest past CEX (0.2.81).
_SECONDARY_HOSTS = frozenset(
    {
        "api.dexscreener.com",
        "api.coingecko.com",
    }
)


class AllowlistHttp:
    def __init__(
        self,
        allowlist: Set[str],
        timeout_ms: int = 12_000,
        cache_ttl_ms: int = 25_000,
        retries: int = 2,
        backoff_ms: int = 200,
        timeout_sec: float | None = None,
        cache_ttl_sec: float | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        max_error_log: int = DEFAULT_MAX_ERROR_LOG,
        secondary_timeout_ms: int | None = None,
    ) -> None:
        if timeout_sec is not None:
            timeout_ms = int(timeout_sec * 1000)
        if cache_ttl_sec is not None:
            cache_ttl_ms = int(cache_ttl_sec * 1000)
        self._allow = frozenset(allowlist)
        self._timeout_ms = int(timeout_ms)
        self._ttl_ms = int(cache_ttl_ms)
        self._retries = max(0, int(retries))
        self._backoff_ms = max(1, int(backoff_ms))
        # Floor 1 KiB so unit tests can use tight caps; config validates ≥64 KiB.
        self._max_response_bytes = max(1024, int(max_response_bytes))
        self._max_cache = max(8, int(max_cache_entries))
        self._max_errors = max(8, int(max_error_log))
        # Optional tighter read timeout for Dex/CoinGecko (None = use default).
        self.secondary_timeout_ms: int | None = (
            int(secondary_timeout_ms) if secondary_timeout_ms is not None else None
        )
        self._cache: Dict[str, tuple[int, Any]] = {}
        self._lock = threading.Lock()
        read_s = self._timeout_ms / 1000.0
        connect_s = min(5.0, max(1.0, read_s / 2.0))
        self._client = httpx.Client(
            timeout=httpx.Timeout(read_s, connect=connect_s),
            verify=True,
            follow_redirects=False,
            headers={"User-Agent": "DUP-Market-Radar/0.2.81"},
        )
        self.errors: list[str] = []
        self.stats: Dict[str, int] = {
            "gets": 0,
            "retries": 0,
            "cache_hits": 0,
            "rate_limited": 0,
            "response_too_large": 0,
            "cache_evictions": 0,
            "scheme_refused": 0,
        }

    def close(self) -> None:
        self._client.close()

    def _push_error(self, msg: str) -> None:
        """Ring-buffer error strings — never unbounded growth."""
        safe = redact_text(str(msg))[:256]
        self.errors.append(safe)
        overflow = len(self.errors) - self._max_errors
        if overflow > 0:
            del self.errors[:overflow]

    def _prune_cache_unlocked(self, now_ms: int) -> None:
        """Drop expired entries; if still over cap, drop oldest by timestamp."""
        expired = [
            k
            for k, (ts, _body) in self._cache.items()
            if now_ms - ts >= self._ttl_ms
        ]
        for k in expired:
            self._cache.pop(k, None)
            self.stats["cache_evictions"] += 1
        while len(self._cache) > self._max_cache:
            oldest_key = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
            self._cache.pop(oldest_key, None)
            self.stats["cache_evictions"] += 1

    def _decode_json(self, resp: httpx.Response, host: str) -> Any:
        try:
            return read_capped_json(
                resp, self._max_response_bytes, label=host or "http"
            )
        except RuntimeError as exc:
            if "too large" in str(exc):
                self.stats["response_too_large"] += 1
            raise

    def get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> Any:
        parsed = urlparse(url)
        if (parsed.scheme or "").lower() != "https":
            self.stats["scheme_refused"] += 1
            msg = f"scheme refused (https only): {parsed.scheme or 'missing'}"
            self._push_error(msg)
            raise PermissionError(msg)
        host = parsed.hostname or ""
        if host not in self._allow:
            msg = f"host refused (not allowlisted): {host}"
            self._push_error(msg)
            raise PermissionError(msg)

        cache_key = url if not params else f"{url}?{sorted(params.items())}"
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._prune_cache_unlocked(now_ms)
            if use_cache and cache_key in self._cache:
                ts_ms, body = self._cache[cache_key]
                if now_ms - ts_ms < self._ttl_ms:
                    self.stats["cache_hits"] += 1
                    return body

        sec_ms = self.secondary_timeout_ms
        use_sec = (
            sec_ms is not None
            and int(sec_ms) > 0
            and host in _SECONDARY_HOSTS
        )
        req_timeout: httpx.Timeout | None = None
        if use_sec:
            read_s = max(0.5, float(int(sec_ms)) / 1000.0)
            connect_s = min(2.0, max(0.5, read_s / 2.0))
            req_timeout = httpx.Timeout(read_s, connect=connect_s)
            # Soft wall: one shot — retries would defeat the ingest bound.
            attempts = 1
        else:
            attempts = self._retries + 1
        last_exc: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                # Network I/O outside lock — httpx.Client is thread-safe; lock only
                # protects stats/cache (was serializing all GETs → ~40s ensure).
                with self._lock:
                    self.stats["gets"] += 1
                if req_timeout is not None:
                    resp = self._client.get(
                        url, params=params, timeout=req_timeout
                    )
                else:
                    resp = self._client.get(url, params=params)
                status = resp.status_code
                if status == 429:
                    with self._lock:
                        self.stats["rate_limited"] += 1
                if status == 429 or status >= 500:
                    body_text = redact_text(resp.text[:200])[:200]
                    last_exc = httpx.HTTPStatusError(
                        f"{status} {body_text}",
                        request=resp.request,
                        response=resp,
                    )
                    retryable = attempt < attempts - 1
                    if retryable:
                        with self._lock:
                            self.stats["retries"] += 1
                        delay = self._backoff_ms * (2**attempt)
                        logger.warning(
                            "soft-refuse retry host=%s status=%s attempt=%s sleep_ms=%s",
                            host,
                            status,
                            attempt + 1,
                            delay,
                        )
                        time.sleep(delay / 1000.0)
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                body = self._decode_json(resp, host)
                now_put = int(time.time() * 1000)
                with self._lock:
                    self._cache[cache_key] = (now_put, body)
                    self._prune_cache_unlocked(now_put)
                return body
            except PermissionError:
                raise
            except httpx.HTTPStatusError as exc:
                # 4xx (except 429, handled above): fail closed — no retry storm.
                last_exc = exc
                code = int(exc.response.status_code) if exc.response is not None else 0
                if 400 <= code < 500 and code != 429:
                    msg = f"{host}: {redact_exc(exc, max_len=200)}"
                    with self._lock:
                        self._push_error(msg)
                    logger.warning("GET failed %s", msg)
                    raise
                if attempt < attempts - 1:
                    with self._lock:
                        self.stats["retries"] += 1
                    delay = self._backoff_ms * (2**attempt)
                    logger.warning(
                        "GET retry host=%s attempt=%s sleep_ms=%s err=%s",
                        host,
                        attempt + 1,
                        delay,
                        redact_exc(exc, max_len=256),
                    )
                    time.sleep(delay / 1000.0)
                    continue
                msg = f"{host}: {redact_exc(exc, max_len=200)}"
                with self._lock:
                    self._push_error(msg)
                logger.warning("GET failed %s", msg)
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts - 1:
                    with self._lock:
                        self.stats["retries"] += 1
                    delay = self._backoff_ms * (2**attempt)
                    logger.warning(
                        "GET retry host=%s attempt=%s sleep_ms=%s err=%s",
                        host,
                        attempt + 1,
                        delay,
                        redact_exc(exc, max_len=256),
                    )
                    time.sleep(delay / 1000.0)
                    continue
                msg = f"{host}: {redact_exc(exc, max_len=200)}"
                with self._lock:
                    self._push_error(msg)
                logger.warning("GET failed %s", msg)
                raise

        msg = f"{host}: exhausted retries ({redact_exc(last_exc, max_len=200) if last_exc else '?'})"
        with self._lock:
            self._push_error(msg)
        raise RuntimeError(msg)
