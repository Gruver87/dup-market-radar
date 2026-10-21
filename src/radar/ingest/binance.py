from __future__ import annotations

import logging
from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num, quote_volume_sort_key
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.binance")


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


def fetch_binance(http: AllowlistHttp) -> Dict[str, Any]:
    spot = http.get_json("https://api.binance.com/api/v3/ticker/24hr")
    futures = http.get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    premium = http.get_json("https://fapi.binance.com/fapi/v1/premiumIndex")
    oi_rows: List[Dict[str, Any]] = []
    spot_rows = [_norm_spot(r) for r in spot if isinstance(r, dict)]
    fut_rows = [_norm_fut(r) for r in futures if isinstance(r, dict)]
    funding = {_p["symbol"]: _p for _p in premium if isinstance(_p, dict) and "symbol" in _p}

    # Soft-cap OI fan-out (was 80 sequential GETs) — top by quote volume only.
    _OI_SAMPLE_N = 24
    fut_sorted = sorted(fut_rows, key=quote_volume_sort_key, reverse=True)[:_OI_SAMPLE_N]
    for row in fut_sorted:
        sym = row["symbol"]
        try:
            oi = http.get_json(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": sym},
                use_cache=True,
            )
            row["open_interest"] = keep_num(oi.get("openInterest"))
            oi_rows.append({"symbol": sym, "open_interest": row["open_interest"]})
        except Exception as exc:  # noqa: BLE001
            row["open_interest"] = None
            logger.warning(
                "binance OI soft-fail %s: %s", sym, redact_exc(exc, max_len=128)
            )

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

    exchange_info = http.get_json("https://api.binance.com/api/v3/exchangeInfo")
    listings = _extract_listings(exchange_info)
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
    whales = _sample_whales(http, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    return {
        "venue": "binance",
        "spot": spot_rows,
        "futures": fut_rows,
        "listings": listings,
        "whales": whales,
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


def _sample_whales(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    prints: List[Dict[str, Any]] = []
    endpoints = (
        ("https://api.binance.com/api/v3/aggTrades", "spot"),
        ("https://fapi.binance.com/fapi/v1/aggTrades", "futures"),
    )
    for sym in symbols:
        for url, market in endpoints:
            try:
                trades = http.get_json(
                    url,
                    params={"symbol": sym, "limit": 200},
                    use_cache=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "binance whale soft-fail %s %s: %s",
                    market,
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
                        "market": market,
                    }
                )
    return prints
