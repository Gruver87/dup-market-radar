from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num, quote_volume_sort_key
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.binance")

_OI_SAMPLE_N = 24
_OI_PARALLEL_WORKERS = 8


def trading_symbols_from_exchange_info(info: Dict[str, Any]) -> set[str]:
    """Symbols with status TRADING — 24hr ticker still lists BREAK (delisted) ghosts."""
    out: set[str] = set()
    for s in info.get("symbols") or []:
        if not isinstance(s, dict):
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol")
        if isinstance(sym, str) and sym:
            out.add(sym)
    return out


def filter_rows_to_trading(
    rows: List[Dict[str, Any]], trading: set[str]
) -> List[Dict[str, Any]]:
    """Drop non-TRADING rows when trading set is non-empty (else keep all — soft)."""
    if not trading:
        return rows
    return [r for r in rows if str(r.get("symbol") or "") in trading]


def _bootstrap_binance(
    http: AllowlistHttp,
) -> Tuple[Any, Any, Any, Any]:
    """Parallel independent Binance payloads (spot/fut/premium/exchangeInfo)."""

    def _spot() -> Any:
        return http.get_json("https://api.binance.com/api/v3/ticker/24hr")

    def _fut() -> Any:
        return http.get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")

    def _prem() -> Any:
        return http.get_json("https://fapi.binance.com/fapi/v1/premiumIndex")

    def _info() -> Any:
        return http.get_json("https://api.binance.com/api/v3/exchangeInfo")

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_spot = pool.submit(_spot)
        f_fut = pool.submit(_fut)
        f_prem = pool.submit(_prem)
        f_info = pool.submit(_info)
        return f_spot.result(), f_fut.result(), f_prem.result(), f_info.result()


def fetch_binance(http: AllowlistHttp) -> Dict[str, Any]:
    spot, futures, premium, exchange_info = _bootstrap_binance(http)
    oi_rows: List[Dict[str, Any]] = []
    spot_list = spot if isinstance(spot, list) else []
    fut_list = futures if isinstance(futures, list) else []
    prem_list = premium if isinstance(premium, list) else []
    spot_rows = [_norm_spot(r) for r in spot_list if isinstance(r, dict)]
    fut_rows = [_norm_fut(r) for r in fut_list if isinstance(r, dict)]
    funding = {
        _p["symbol"]: _p
        for _p in prem_list
        if isinstance(_p, dict) and "symbol" in _p
    }

    # Soft-cap OI fan-out — top by quote volume; parallel GETs (was serial ~3–6s).
    fut_sorted = sorted(fut_rows, key=quote_volume_sort_key, reverse=True)[:_OI_SAMPLE_N]
    oi_by_sym = _fetch_oi_parallel(http, [str(r.get("symbol") or "") for r in fut_sorted])
    for row in fut_sorted:
        sym = str(row.get("symbol") or "")
        oi_s = oi_by_sym.get(sym)
        row["open_interest"] = oi_s
        if oi_s is not None:
            oi_rows.append({"symbol": sym, "open_interest": oi_s})
        prem = funding.get(sym, {})
        row["funding_rate"] = keep_num(prem.get("lastFundingRate"))
        row["mark_price"] = keep_num(prem.get("markPrice"))
        row["index_price"] = keep_num(prem.get("indexPrice"))

    for row in fut_rows:
        if "funding_rate" not in row:
            prem = funding.get(row["symbol"], {})
            row["funding_rate"] = keep_num(prem.get("lastFundingRate"))
            row["mark_price"] = keep_num(prem.get("markPrice"))
            row["index_price"] = keep_num(prem.get("indexPrice"))
            row.setdefault("open_interest", None)

    listings = _extract_listings(
        exchange_info if isinstance(exchange_info, dict) else {}
    )
    # 24hr ticker includes BREAK (delisted) pairs with residual quoteVolume —
    # windowed /klines return [] → spot_mtf stuck at 0 on score top (NFP/VIC…).
    trading = trading_symbols_from_exchange_info(
        exchange_info if isinstance(exchange_info, dict) else {}
    )
    if trading:
        before = len(spot_rows)
        spot_rows = filter_rows_to_trading(spot_rows, trading)
        dropped = before - len(spot_rows)
        if dropped:
            logger.info(
                "binance spot drop non-TRADING=%s keep=%s", dropped, len(spot_rows)
            )
    else:
        logger.warning("binance exchangeInfo TRADING set empty — spot unfiltered")
    # Whales: KuCoin-primary only (0.2.71) — skip Binance aggTrades fan-out.

    return {
        "venue": "binance",
        "spot": spot_rows,
        "futures": fut_rows,
        "listings": listings,
        "whales": [],
        "oi_sample": oi_rows,
    }


def _norm_spot(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "venue": "binance",
        "market": "spot",
        "symbol": r.get("symbol"),
        "price": keep_num(r.get("lastPrice")),
        "change_pct": keep_num(r.get("priceChangePercent")),
        "quote_volume": keep_num(r.get("quoteVolume")),
        "high": keep_num(r.get("highPrice")),
        "low": keep_num(r.get("lowPrice")),
        "bid": keep_num(r.get("bidPrice")),
        "ask": keep_num(r.get("askPrice")),
    }


def _norm_fut(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "venue": "binance",
        "market": "futures",
        "symbol": r.get("symbol"),
        "price": keep_num(r.get("lastPrice")),
        "change_pct": keep_num(r.get("priceChangePercent")),
        "quote_volume": keep_num(r.get("quoteVolume")),
        "high": keep_num(r.get("highPrice")),
        "low": keep_num(r.get("lowPrice")),
    }


def _extract_listings(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in info.get("symbols") or []:
        if not isinstance(s, dict):
            continue
        if s.get("status") != "TRADING":
            continue
        onboard = s.get("onboardDate") or s.get("listTime")
        if onboard is None:
            continue
        try:
            ts = int(onboard)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "venue": "binance",
                "market": "spot",
                "symbol": s.get("symbol"),
                "base": s.get("baseAsset"),
                "quote": s.get("quoteAsset"),
                "listed_at_ms": ts,
            }
        )
    return out


def _fetch_oi_parallel(
    http: AllowlistHttp, symbols: List[str]
) -> Dict[str, Optional[str]]:
    """Fetch openInterest for symbols; soft-fail per symbol. Preserves input coverage."""
    syms = [s for s in symbols if s]
    out: Dict[str, Optional[str]] = {s: None for s in syms}
    if not syms:
        return out

    def _one(sym: str) -> Tuple[str, Optional[str]]:
        try:
            oi = http.get_json(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": sym},
                use_cache=True,
            )
            return sym, keep_num(oi.get("openInterest")) if isinstance(oi, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "binance OI soft-fail %s: %s", sym, redact_exc(exc, max_len=128)
            )
            return sym, None

    workers = max(1, min(_OI_PARALLEL_WORKERS, len(syms)))
    if workers == 1:
        for s in syms:
            k, v = _one(s)
            out[k] = v
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, s) for s in syms]
        for fut in as_completed(futs):
            k, v = fut.result()
            out[k] = v
    return out


def _sample_whales(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    """Spot aggTrades only — futures path dropped (0.2.70: cut dual-market fan-out).

    Primary whale book remains KuCoin; Binance is a thin supplemental size proxy.
    """
    prints: List[Dict[str, Any]] = []
    url = "https://api.binance.com/api/v3/aggTrades"
    for sym in symbols:
        try:
            trades = http.get_json(
                url,
                params={"symbol": sym, "limit": 200},
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "binance whale soft-fail spot %s: %s",
                sym,
                redact_exc(exc, max_len=256),
            )
            continue
        for t in trades or []:
            price = keep_num(t.get("p"))
            qty = keep_num(t.get("q"))
            if price is None or qty is None:
                continue
            prints.append(
                {
                    "venue": "binance",
                    "symbol": sym,
                    "price": price,
                    "qty": qty,
                    "side": "sell" if t.get("m") else "buy",
                    "ts_ms": t.get("T"),
                    "market": "spot",
                }
            )
    return prints
