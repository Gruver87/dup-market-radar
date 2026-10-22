"""KuCoin public ingest — primary venue for DUP Market Radar."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from radar.http_client import AllowlistHttp
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.kucoin")


def _bootstrap_kucoin(
    http: AllowlistHttp,
) -> Tuple[Any, Any, Any]:
    """Parallel allTickers + contracts/active + spot symbols meta."""

    def _tickers() -> Any:
        return http.get_json("https://api.kucoin.com/api/v1/market/allTickers")

    def _contracts() -> Any:
        return http.get_json("https://api-futures.kucoin.com/api/v1/contracts/active")

    def _symbols() -> Any:
        return http.get_json("https://api.kucoin.com/api/v2/symbols")

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_t = pool.submit(_tickers)
        f_c = pool.submit(_contracts)
        f_s = pool.submit(_symbols)
        # Tickers are hard-required; contracts/symbols soft-fail.
        spot_raw = f_t.result()
        try:
            contracts = f_c.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin futures tickers soft-fail: %s", redact_exc(exc, max_len=256)
            )
            contracts = None
        try:
            symbols = f_s.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin spot listings soft-fail: %s", redact_exc(exc, max_len=256)
            )
            symbols = None
        return spot_raw, contracts, symbols


def whale_symbols_kucoin(raw: Optional[List[Any]] = None) -> List[str]:
    """Map Radar USDT symbols (BTCUSDT) → KuCoin spot form (BTC-USDT)."""
    src = list(raw or []) or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out: List[str] = []
    seen: set[str] = set()
    for item in src:
        s = str(item or "").upper().replace("-", "").replace("_", "")
        if not s.endswith("USDT") or len(s) <= 4:
            continue
        pair = f"{s[:-4]}-USDT"
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
        if len(out) >= 16:
            break
    return out or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def fetch_kucoin(
    http: AllowlistHttp, *, whale_symbols: Optional[List[str]] = None
) -> Dict[str, Any]:
    """KuCoin tickers + listings + whales.

    MTF short-windows are **not** attached here — owned by
    ``ensure_universe_short_windows`` (budget + reuse TTL). Ingest-time
    attach previously burned ~40 unbudgeted kline GETs per cex refresh.
    """
    watch = whale_symbols_kucoin(whale_symbols)
    # Whales overlap with bootstrap (histories ∥ tickers/contracts/symbols).
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_boot = pool.submit(_bootstrap_kucoin, http)
        f_whales = pool.submit(_sample_whales, http, watch)
        spot_raw, contracts, symbols_info = f_boot.result()
        whales = f_whales.result()
    tickers = ((spot_raw or {}).get("data") or {}).get("ticker") or []
    spot_rows = [_norm_spot(r) for r in tickers if isinstance(r, dict)]

    fut_rows: List[Dict[str, Any]] = []
    contracts_data: List[Any] = []
    if isinstance(contracts, dict):
        data = contracts.get("data") or []
        if isinstance(data, list):
            contracts_data = data
            fut_rows = [
                _norm_fut(r)
                for r in data
                if isinstance(r, dict) and r.get("status") == "Open"
            ]

    listings = _listings_from_payloads(
        symbols_info if isinstance(symbols_info, dict) else None,
        contracts_data=contracts_data,
    )

    return {
        "venue": "kucoin",
        "spot": spot_rows,
        "futures": fut_rows,
        "listings": listings,
        "whales": whales,
    }


def normalize_symbol(sym: str | None, *, futures: bool = False) -> str:
    """Map KuCoin symbols to Radar USDT form (BTCUSDT). XBT→BTC, strip perp M suffix."""
    if not sym:
        return ""
    s = str(sym).upper().replace("-", "").replace("_", "")[:32]
    if futures and s.endswith("USDTM"):
        s = s[:-1]  # USDTM → USDT
    if s.startswith("XBT"):
        s = ("BTC" + s[3:])[:32]
    return s


def _norm_spot(r: Dict[str, Any]) -> Dict[str, Any]:
    from radar.ingest.parse import fraction_to_pct_str, keep_num

    raw = r.get("symbol")
    # changeRate is fraction; priceChangePercent may be fraction or %
    chg = fraction_to_pct_str(r.get("changeRate"))
    if chg is None:
        chg = fraction_to_pct_str(r.get("priceChangePercent"))
    return {
        "venue": "kucoin",
        "market": "spot",
        "symbol": normalize_symbol(raw),
        "symbol_raw": raw,
        "price": keep_num(r.get("last")),
        "change_pct": chg,
        "quote_volume": keep_num(r.get("volValue")),
        "high": keep_num(r.get("high")),
        "low": keep_num(r.get("low")),
        "bid": keep_num(r.get("buy")),
        "ask": keep_num(r.get("sell")),
    }


def _norm_fut(r: Dict[str, Any]) -> Dict[str, Any]:
    from radar.ingest.parse import fraction_to_pct_str, keep_num
    from radar.money import dec_product_str

    raw = r.get("symbol")
    oi = keep_num(r.get("openInterest"))
    mark = keep_num(r.get("markPrice") or r.get("lastTradePrice"))
    mult = keep_num(r.get("multiplier")) or "1"
    oi_usdt = None
    if oi is not None and mark is not None:
        mid = dec_product_str(oi, mult)
        oi_usdt = dec_product_str(mid, mark) if mid is not None else None
    return {
        "venue": "kucoin",
        "market": "futures",
        "symbol": normalize_symbol(raw, futures=True),
        "symbol_raw": raw,
        "price": keep_num(r.get("lastTradePrice")) or mark,
        "change_pct": fraction_to_pct_str(r.get("priceChgPct")),
        "quote_volume": keep_num(r.get("turnoverOf24h")),
        "funding_rate": keep_num(r.get("fundingFeeRate")),
        "open_interest": oi,
        "open_interest_usdt": oi_usdt,
        "mark_price": mark,
        "index_price": keep_num(r.get("indexPrice")),
        "listed_at_ms": r.get("firstOpenDate"),
    }


def _listings_from_payloads(
    symbols_info: Optional[Dict[str, Any]],
    *,
    contracts_data: List[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build listings from already-fetched symbols + contracts payloads."""
    out: List[Dict[str, Any]] = []
    if isinstance(symbols_info, dict):
        for s in symbols_info.get("data") or []:
            if not isinstance(s, dict):
                continue
            if not s.get("enableTrading"):
                continue
            ts = s.get("tradingStartTime")
            if ts is None:
                continue
            try:
                ts_i = int(ts)
            except (TypeError, ValueError):
                continue
            raw = s.get("symbol")
            out.append(
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": normalize_symbol(raw),
                    "symbol_raw": raw,
                    "base": s.get("baseCurrency"),
                    "quote": s.get("quoteCurrency"),
                    "listed_at_ms": ts_i,
                }
            )
    for r in contracts_data or []:
        if not isinstance(r, dict) or r.get("status") != "Open":
            continue
        ts = r.get("firstOpenDate")
        if ts is None:
            continue
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            continue
        raw = r.get("symbol")
        out.append(
            {
                "venue": "kucoin",
                "market": "futures",
                "symbol": normalize_symbol(raw, futures=True),
                "symbol_raw": raw,
                "base": r.get("baseCurrency"),
                "quote": r.get("quoteCurrency"),
                "listed_at_ms": ts_i,
            }
        )
    return out


def _listings(
    http: AllowlistHttp, *, contracts_data: List[Any] | None = None
) -> List[Dict[str, Any]]:
    """Spot symbols + futures firstOpenDate.

    Pass ``contracts_data`` from the already-fetched ``contracts/active`` payload
    to avoid a duplicate GET (0.2.70). Prefer ``_listings_from_payloads`` when
    bootstrap already fetched symbols.
    """
    try:
        info = http.get_json("https://api.kucoin.com/api/v2/symbols")
    except Exception as exc:  # noqa: BLE001
        logger.warning("kucoin spot listings soft-fail: %s", redact_exc(exc, max_len=256))
        info = None
    fut_src = contracts_data
    if fut_src is None:
        try:
            contracts = http.get_json(
                "https://api-futures.kucoin.com/api/v1/contracts/active"
            )
            raw_list = contracts.get("data") or []
            fut_src = raw_list if isinstance(raw_list, list) else []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin futures listings soft-fail: %s", redact_exc(exc, max_len=256)
            )
            fut_src = []
    return _listings_from_payloads(
        info if isinstance(info, dict) else None,
        contracts_data=fut_src,
    )


def _whale_prints_from_histories(
    sym: str, data: Any
) -> List[Dict[str, Any]]:
    prints: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return prints
    for t in data.get("data") or []:
        price = t.get("price")
        qty = t.get("size")
        if price is None or qty is None or price == "" or qty == "":
            continue
        # KuCoin trade time is ns sometimes
        ts = t.get("time")
        try:
            ts_i = int(ts)
            if ts_i > 10_000_000_000_000:  # ns
                ts_i //= 1_000_000
        except (TypeError, ValueError):
            ts_i = None
        prints.append(
            {
                "venue": "kucoin",
                "symbol": normalize_symbol(sym),
                "symbol_raw": sym,
                "price": price,  # string → enrich → price_i / notional_i
                "qty": qty,
                "side": (t.get("side") or "").lower(),
                "ts_ms": ts_i,
                "market": "spot",
            }
        )
    return prints


def _sample_whales(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    """Parallel histories fan-out for BTC/ETH/SOL (KuCoin-primary whales)."""
    syms = [s for s in symbols if s]
    if not syms:
        return []

    def _one(sym: str) -> List[Dict[str, Any]]:
        try:
            data = http.get_json(
                "https://api.kucoin.com/api/v1/market/histories",
                params={"symbol": sym},
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin whale soft-fail %s: %s", sym, redact_exc(exc, max_len=256)
            )
            return []
        return _whale_prints_from_histories(sym, data)

    if len(syms) == 1:
        return _one(syms[0])
    prints: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(syms))) as pool:
        futs = {pool.submit(_one, s): s for s in syms}
        for fut in as_completed(futs):
            prints.extend(fut.result())
    return prints
