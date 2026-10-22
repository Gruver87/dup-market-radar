"""Process-lifetime AllowlistHttp — reuse TLS/cache across scans.

Caller must not close the pooled client on every scan. Shutdown closes the pool.
Config fingerprint change recreates the client (allowlist / timeouts / caps).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Set, Tuple

from radar.http_client import DEFAULT_MAX_RESPONSE_BYTES, AllowlistHttp

logger = logging.getLogger("radar.http_pool")

_lock = threading.Lock()
_client: Optional[AllowlistHttp] = None
_fingerprint: Optional[Tuple[Any, ...]] = None


def _fp(
    allow: Set[str],
    *,
    timeout_ms: int,
    cache_ttl_ms: int,
    retries: int,
    backoff_ms: int,
    max_response_bytes: int,
) -> Tuple[Any, ...]:
    return (
        tuple(sorted(allow)),
        int(timeout_ms),
        int(cache_ttl_ms),
        int(retries),
        int(backoff_ms),
        int(max_response_bytes),
    )


def acquire_http(
    allow: Set[str],
    *,
    timeout_ms: int,
    cache_ttl_ms: int,
    retries: int = 2,
    backoff_ms: int = 200,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> AllowlistHttp:
    """Return shared AllowlistHttp for this process (thread-safe get_json)."""
    global _client, _fingerprint
    key = _fp(
        allow,
        timeout_ms=timeout_ms,
        cache_ttl_ms=cache_ttl_ms,
        retries=retries,
        backoff_ms=backoff_ms,
        max_response_bytes=max_response_bytes,
    )
    with _lock:
        if _client is not None and _fingerprint == key:
            return _client
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
            _client = None
        _client = AllowlistHttp(
            allow,
            timeout_ms=timeout_ms,
            cache_ttl_ms=cache_ttl_ms,
            retries=retries,
            backoff_ms=backoff_ms,
            max_response_bytes=max_response_bytes,
        )
        _fingerprint = key
        logger.info("http pool client created ttl_ms=%s", cache_ttl_ms)
        return _client


def stats_delta(http: AllowlistHttp, baseline: Dict[str, int]) -> Dict[str, int]:
    """Per-scan http_stats from shared client counters."""
    out: Dict[str, int] = {}
    for k, v in (http.stats or {}).items():
        out[k] = int(v or 0) - int(baseline.get(k) or 0)
    return out


def close_http_pool() -> None:
    """Shutdown hook — close pooled client."""
    global _client, _fingerprint
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
            _client = None
            _fingerprint = None
            logger.info("http pool closed")
