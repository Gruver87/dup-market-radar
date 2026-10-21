"""Top-100 crypto bubbles — live prices + ~6m (CoinGecko 200d) change.

Integer money only. CoinGecko has no exact 180d window; we use ``200d``
and label it honestly as ``~6m (200d)``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from radar.http_client import read_capped_json
from radar.ingest.circuit import VenueCircuitBreaker
from radar.money import pct_to_bps, price_i, quote_i
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ops.bubbles")

CG_HOST = "api.coingecko.com"
CG_MAX_RESPONSE_BYTES = 4_194_304  # 4 MiB — 100 markets rows
DEFAULT_TOP_N = 100
DEFAULT_CACHE_TTL_MS = 30_000
# Closest CoinGecko window to six months
PERIOD_KEY = "200d"
PERIOD_LABEL = "~6m (200d)"
# Only these hosts may appear in optional image URLs (never render untrusted schemes).
_IMAGE_HOSTS = frozenset(
    {
        "assets.coingecko.com",
        "coin-images.coingecko.com",
        "www.coingecko.com",
    }
)

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_at_ms: int = 0
_last_error: Optional[str] = None
_circuit = VenueCircuitBreaker(fail_threshold=3, open_ms=60_000)


class BubblesError(RuntimeError):
    pass


def _safe_image_url(raw: Any) -> Optional[str]:
    """HTTPS + CoinGecko CDN only — drop javascript:/data:/off-host."""
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) > 256 or not s.startswith("https://"):
        return None
    try:
        parsed = urlparse(s)
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").lower()
    if host not in _IMAGE_HOSTS:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    path = parsed.path or ""
    if ".." in path or "\\" in path:
        return None
    return f"https://{host}{path}"


def _row_from_cg(item: Dict[str, Any], rank: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    sym = str(item.get("symbol") or "").upper().strip()
    if not sym or len(sym) > 16:
        return None
    name = str(item.get("name") or sym)[:64]
    # CoinGecko field name for requested price_change_percentage=200d
    chg = item.get("price_change_percentage_200d_in_currency")
    if chg is None:
        chg = item.get("price_change_percentage_200d")
    pi = price_i(item.get("current_price"))
    # Market cap as USDT micros (integer) — may be large; Python int OK
    mcap_i = quote_i(item.get("market_cap"))
    vol_i = quote_i(item.get("total_volume"))
    chg_bps = pct_to_bps(chg)
    return {
        "rank": int(rank),
        "id": str(item.get("id") or sym).lower()[:64],
        "symbol": sym,
        "name": name,
        "price_i": pi,
        "market_cap_i": mcap_i,
        "volume_24h_i": vol_i,
        "change_6m_bps": chg_bps,  # ~6m via 200d
        "period": PERIOD_KEY,
        "period_label": PERIOD_LABEL,
        "image": _safe_image_url(item.get("image")),
    }


def _fetch_markets(*, top_n: int) -> List[Dict[str, Any]]:
    top_n = max(1, min(250, int(top_n)))
    url = f"https://{CG_HOST}/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": str(top_n),
        "page": "1",
        "sparkline": "false",
        "price_change_percentage": PERIOD_KEY,
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(25.0, connect=5.0),
            verify=True,
            follow_redirects=False,
        ) as client:
            resp = client.get(
                url,
                params=params,
                headers={"User-Agent": "DUP-Market-Radar/0.2.59-bubbles"},
            )
            resp.raise_for_status()
            data = read_capped_json(resp, CG_MAX_RESPONSE_BYTES, label="coingecko-bubbles")
    except Exception as exc:  # noqa: BLE001
        raise BubblesError(redact_exc(exc, max_len=256)) from None
    if not isinstance(data, list):
        raise BubblesError("markets response not a list")
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(data[:top_n], start=1):
        row = _row_from_cg(item if isinstance(item, dict) else {}, i)
        if row:
            out.append(row)
    if not out:
        raise BubblesError("markets empty after parse")
    return out


def get_bubbles(
    *,
    top_n: int = DEFAULT_TOP_N,
    cache_ttl_ms: int = DEFAULT_CACHE_TTL_MS,
    force: bool = False,
) -> Dict[str, Any]:
    """Return top-N bubble book; soft-serve stale cache on upstream fail."""
    global _cache, _cache_at_ms, _last_error
    now = int(time.time() * 1000)
    with _lock:
        if (
            not force
            and _cache is not None
            and (now - _cache_at_ms) < max(1_000, int(cache_ttl_ms))
        ):
            body = dict(_cache)
            body["cache_hit"] = True
            body["age_ms"] = now - _cache_at_ms
            body["circuit"] = _circuit.status()
            return body

    if not _circuit.allow("coingecko"):
        with _lock:
            if _cache is not None:
                body = dict(_cache)
                body["ok"] = True
                body["stale"] = True
                body["error"] = "coingecko circuit open — soft refuse"
                body["cache_hit"] = True
                body["age_ms"] = now - _cache_at_ms
                body["circuit"] = _circuit.status()
                return body
        return {
            "ok": False,
            "product": "DUP Market Radar",
            "source": "coingecko",
            "period": PERIOD_KEY,
            "period_label": PERIOD_LABEL,
            "top_n": 0,
            "generated_at_ms": now,
            "coins": [],
            "stale": False,
            "error": "coingecko circuit open — soft refuse",
            "cache_hit": False,
            "age_ms": 0,
            "circuit": _circuit.status(),
            "disclaimer": "Live mid prices · ~6m = CoinGecko 200d · not financial advice",
        }

    try:
        rows = _fetch_markets(top_n=top_n)
        _circuit.record_ok("coingecko")
        payload = {
            "ok": True,
            "product": "DUP Market Radar",
            "source": "coingecko",
            "period": PERIOD_KEY,
            "period_label": PERIOD_LABEL,
            "top_n": len(rows),
            "generated_at_ms": now,
            "coins": rows,
            "stale": False,
            "error": None,
            "circuit": _circuit.status(),
            "disclaimer": "Live mid prices · ~6m = CoinGecko 200d · not financial advice",
        }
        with _lock:
            _cache = dict(payload)
            _cache_at_ms = now
            _last_error = None
        payload["cache_hit"] = False
        payload["age_ms"] = 0
        return payload
    except BubblesError as exc:
        err = redact_exc(exc, max_len=256)
        _circuit.record_fail("coingecko", reason=err)
        logger.warning("bubbles fetch failed: %s", err)
        with _lock:
            _last_error = err
            if _cache is not None:
                body = dict(_cache)
                body["ok"] = True
                body["stale"] = True
                body["error"] = err
                body["cache_hit"] = True
                body["age_ms"] = now - _cache_at_ms
                body["circuit"] = _circuit.status()
                return body
        return {
            "ok": False,
            "product": "DUP Market Radar",
            "source": "coingecko",
            "period": PERIOD_KEY,
            "period_label": PERIOD_LABEL,
            "top_n": 0,
            "generated_at_ms": now,
            "coins": [],
            "stale": False,
            "error": err,
            "cache_hit": False,
            "age_ms": 0,
            "circuit": _circuit.status(),
            "disclaimer": "Live mid prices · ~6m = CoinGecko 200d · not financial advice",
        }
