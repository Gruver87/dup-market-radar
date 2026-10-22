from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Sequence

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.dexscreener")

_EXPAND_CAP = 12
# DexScreener /latest/dex/tokens/{addr} accepts comma-separated addresses (≤30).
_TOKEN_BATCH_SIZE = 30
_EXPAND_WORKERS = 4
# Soft-skip keyword searches when boosted expands already filled the book.
_SEARCH_SKIP_MIN_ROWS = 24
_SEARCH_QUERIES = ("PEPE", "BONK", "WIF", "DOGE", "POPCAT")


def fetch_meme_pairs(http: AllowlistHttp, chains: List[str]) -> List[Dict[str, Any]]:
    """Boosted / trending DEX pairs — public DexScreener endpoints.

    Token expands use batched ``/tokens/{a,b,...}`` (0.2.75) — one GET for the
    boosted cap instead of N. Keyword searches soft-skipped when boosted fills.
    """
    rows: List[Dict[str, Any]] = []
    try:
        boosted = http.get_json("https://api.dexscreener.com/token-boosts/top/v1")
        if isinstance(boosted, list):
            items = [x for x in boosted[:_EXPAND_CAP] if isinstance(x, dict)]
            rows.extend(_expand_tokens_batched(http, items))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dexscreener boosted soft-fail: %s", redact_exc(exc, max_len=256)
        )

    if len(rows) < _SEARCH_SKIP_MIN_ROWS:
        rows.extend(_search_keywords_parallel(http))
    else:
        logger.info(
            "dexscreener search skipped — boosted rows=%s >= %s",
            len(rows),
            _SEARCH_SKIP_MIN_ROWS,
        )

    chain_set = {c.lower() for c in chains}
    filtered = [
        r
        for r in rows
        if r.get("chain") and (not chain_set or str(r["chain"]).lower() in chain_set)
    ]
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in filtered or rows:
        key = (r.get("pair_address") or r.get("symbol"), r.get("chain"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _token_address_chunks(
    items: Sequence[Dict[str, Any]], *, batch_size: int = _TOKEN_BATCH_SIZE
) -> List[List[str]]:
    """Dedupe token addresses preserving order; chunk for multi-token GET."""
    seen: set[str] = set()
    addrs: List[str] = []
    for item in items:
        addr = item.get("tokenAddress")
        if not isinstance(addr, str) or not addr.strip():
            continue
        a = addr.strip()
        if a in seen:
            continue
        seen.add(a)
        addrs.append(a)
    size = max(1, min(30, int(batch_size)))
    return [addrs[i : i + size] for i in range(0, len(addrs), size)]


def _expand_tokens_batched(
    http: AllowlistHttp, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Expand boosted tokens via comma-batched /tokens/ GETs."""
    chunks = _token_address_chunks(items)
    if not chunks:
        return []

    def _one(chunk: List[str]) -> List[Dict[str, Any]]:
        joined = ",".join(chunk)
        try:
            data = http.get_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{joined}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dexscreener token batch soft-fail n=%s: %s",
                len(chunk),
                redact_exc(exc, max_len=128),
            )
            return []
        pairs = data.get("pairs") if isinstance(data, dict) else None
        if not isinstance(pairs, list):
            return []
        # Cap pairs per batch to avoid meme-book bloat (≤5 per token × cap).
        cap = max(8, len(chunk) * 5)
        return [_norm_pair(p) for p in pairs[:cap] if isinstance(p, dict)]

    if len(chunks) == 1:
        return _one(chunks[0])
    workers = max(1, min(_EXPAND_WORKERS, len(chunks)))
    parts: List[List[Dict[str, Any]]] = [[] for _ in chunks]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futs):
            parts[futs[fut]] = fut.result()
    out: List[Dict[str, Any]] = []
    for part in parts:
        out.extend(part)
    return out


def _search_keywords_parallel(http: AllowlistHttp) -> List[Dict[str, Any]]:
    queries = list(_SEARCH_QUERIES)
    workers = max(1, min(len(queries), _EXPAND_WORKERS))

    def _one(q: str) -> List[Dict[str, Any]]:
        try:
            data = http.get_json(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": q},
            )
            return [_norm_pair(p) for p in (data.get("pairs") or [])[:8]]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dexscreener search soft-fail q=%s: %s",
                q,
                redact_exc(exc, max_len=256),
            )
            return []

    if workers == 1:
        out: List[Dict[str, Any]] = []
        for q in queries:
            out.extend(_one(q))
        return out
    parts: List[List[Dict[str, Any]]] = [[] for _ in queries]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, q): i for i, q in enumerate(queries)}
        for fut in as_completed(futs):
            parts[futs[fut]] = fut.result()
    out2: List[Dict[str, Any]] = []
    for part in parts:
        out2.extend(part)
    return out2


def _norm_pair(p: Dict[str, Any]) -> Dict[str, Any]:
    base = (p.get("baseToken") or {}).get("symbol")
    quote = (p.get("quoteToken") or {}).get("symbol")
    liq = (p.get("liquidity") or {}).get("usd")
    vol = (p.get("volume") or {}).get("h24")
    created = p.get("pairCreatedAt")
    return {
        "venue": "dexscreener",
        "market": "dex",
        "symbol": f"{base}/{quote}" if base else p.get("pairAddress"),
        "base": base,
        "quote": quote,
        "chain": p.get("chainId"),
        "pair_address": p.get("pairAddress"),
        "price": keep_num(p.get("priceUsd")),
        "change_pct": keep_num((p.get("priceChange") or {}).get("h24")),
        "change_pct_5m": keep_num((p.get("priceChange") or {}).get("m5")),
        "change_pct_1h": keep_num((p.get("priceChange") or {}).get("h1")),
        "liquidity_usd": keep_num(liq),
        "quote_volume": keep_num(vol),
        "pair_created_at_ms": int(created) if created else None,
        "url": p.get("url"),
    }
