from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.dexscreener")

_EXPAND_CAP = 12
_EXPAND_WORKERS = 6
# Soft-skip keyword searches when boosted expands already filled the book.
_SEARCH_SKIP_MIN_ROWS = 24
_SEARCH_QUERIES = ("PEPE", "BONK", "WIF", "DOGE", "POPCAT")


def fetch_meme_pairs(http: AllowlistHttp, chains: List[str]) -> List[Dict[str, Any]]:
    """Boosted / trending DEX pairs — public DexScreener endpoints.

    Token expands run in a small thread pool. Keyword searches are skipped when
    boosted expands already yield enough rows (0.2.71).
    """
    rows: List[Dict[str, Any]] = []
    try:
        boosted = http.get_json("https://api.dexscreener.com/token-boosts/top/v1")
        if isinstance(boosted, list):
            items = [x for x in boosted[:_EXPAND_CAP] if isinstance(x, dict)]
            rows.extend(_expand_tokens_parallel(http, items))
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


def _expand_tokens_parallel(
    http: AllowlistHttp, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not items:
        return []
    workers = max(1, min(_EXPAND_WORKERS, len(items)))
    if workers == 1:
        out: List[Dict[str, Any]] = []
        for item in items:
            out.extend(_expand_token(http, item))
        return out
    chunks: List[List[Dict[str, Any]]] = [[] for _ in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_expand_token, http, item): i for i, item in enumerate(items)}
        for fut in as_completed(futs):
            chunks[futs[fut]] = fut.result()
    out: List[Dict[str, Any]] = []
    for part in chunks:
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


def _expand_token(http: AllowlistHttp, item: Dict[str, Any]) -> List[Dict[str, Any]]:
    chain = item.get("chainId")
    addr = item.get("tokenAddress")
    if not chain or not addr:
        return []
    try:
        data = http.get_json(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dexscreener token expand soft-fail: %s", redact_exc(exc, max_len=256)
        )
        return []
    return [_norm_pair(p) for p in (data.get("pairs") or [])[:5]]


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
