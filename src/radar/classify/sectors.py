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


# USDT-M bases that are commodities / FX / equity indices — not crypto alts.
# Override / extend via sectors.futures.exclude_bases in config.
DEFAULT_NON_CRYPTO_FUT_BASES = frozenset(
    {
        "XAU",
        "XAG",
        "XPD",
        "XPT",
        "USOIL",
        "UKOIL",
        "WTI",
        "BRENT",
        "NATGAS",
        "COPPER",
        "ALUMINIUM",
        "ALUMINUM",
        "NICKEL",
        "WHEAT",
        "CORN",
        "SOYBEAN",
        "COTTON",
        "SUGAR",
        "COFFEE",
        "SPX",
        "NDX",
        "DJI",
        "HSI",
        "FTSE",
        "GER40",
        "US30",
        "NAS100",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCNH",
    }
)


def is_non_crypto_futures_symbol(
    symbol: str | None, extra_bases: Iterable[str] | None = None
) -> bool:
    """True if futures symbol base is commodity/FX/index noise for crypto radar."""
    base = base_asset(symbol)
    if not base:
        return False
    blocked = set(DEFAULT_NON_CRYPTO_FUT_BASES)
    if extra_bases:
        blocked |= {str(x).upper() for x in extra_bases if x}
    return base in blocked
