from __future__ import annotations

import logging
from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.dexscreener")


def fetch_meme_pairs(http: AllowlistHttp, chains: List[str]) -> List[Dict[str, Any]]:
    """Boosted / trending DEX pairs — public DexScreener endpoints."""
    rows: List[Dict[str, Any]] = []
    try:
        boosted = http.get_json("https://api.dexscreener.com/token-boosts/top/v1")
        if isinstance(boosted, list):
            # Soft-cap expand fan-out (each item = extra GET).
            for item in boosted[:12]:
                rows.extend(_expand_token(http, item))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dexscreener boosted soft-fail: %s", redact_exc(exc, max_len=256)
        )

    for q in ("PEPE", "BONK", "WIF", "DOGE", "POPCAT"):
        try:
            data = http.get_json(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": q},
            )
            for p in (data.get("pairs") or [])[:8]:
                rows.append(_norm_pair(p))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dexscreener search soft-fail q=%s: %s",
                q,
                redact_exc(exc, max_len=256),
            )
            continue

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
