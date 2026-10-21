from __future__ import annotations

from typing import Iterable, Set


def base_asset(symbol: str | None) -> str:
    if not symbol:
        return ""
    s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    for q in ("USDT", "USDC", "BUSD", "FDUSD", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


def is_usdt_pair(symbol: str | None) -> bool:
    if not symbol:
        return False
    s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    return s.endswith("USDT")


def is_stable_symbol(symbol: str | None, stables: Iterable[str]) -> bool:
    base = base_asset(symbol)
    return base in {x.upper() for x in stables} or (symbol or "").upper() in {
        x.upper() for x in stables
    }


def is_meme_symbol(symbol: str | None, keywords: Iterable[str]) -> bool:
    base = base_asset(symbol)
    keys: Set[str] = {k.upper() for k in keywords}
    if base in keys:
        return True
    return any(k in base for k in keys if len(k) >= 4)
