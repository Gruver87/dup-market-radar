from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from radar.http_client import AllowlistHttp
from radar.ingest.parse import fraction_to_pct_str, keep_num
from radar.money import dec_product_str
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.bybit")

# Re-export for tests
_bybit_change_pct_str = fraction_to_pct_str


def _bootstrap_bybit(
    http: AllowlistHttp,
) -> Tuple[Any, Any, Any]:
    """Parallel spot tickers ∥ linear tickers ∥ spot instruments-info."""

    def _spot() -> Any:
        return http.get_json(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "spot"},
        )

    def _linear() -> Any:
        return http.get_json(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
        )

    def _instruments() -> Any:
        return http.get_json(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "spot", "limit": 1000},
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_s = pool.submit(_spot)
        f_l = pool.submit(_linear)
        f_i = pool.submit(_instruments)
        spot = f_s.result()
        try:
            linear = f_l.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit linear soft-fail: %s", redact_exc(exc, max_len=128))
            linear = None
        try:
            instruments = f_i.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bybit instruments soft-fail: %s", redact_exc(exc, max_len=128)
            )
            instruments = None
        return spot, linear, instruments


def fetch_bybit(http: AllowlistHttp) -> Dict[str, Any]:
    spot, linear, instruments = _bootstrap_bybit(http)
    spot_rows = [
        _norm(r, "spot")
        for r in ((spot or {}).get("result") or {}).get("list") or []
        if isinstance(r, dict)
    ]
    fut_rows = [
        _norm_fut(r)
        for r in ((linear or {}).get("result") or {}).get("list") or []
        if isinstance(r, dict)
    ]
    listings = _listings(
        ((instruments or {}).get("result") or {}).get("list") or []
        if isinstance(instruments, dict)
        else []
    )

    # Whales: KuCoin-primary only (0.2.70) — skip Bybit recent-trade fan-out.
    return {
        "venue": "bybit",
        "spot": spot_rows,
        "futures": fut_rows,
        "listings": listings,
        "whales": [],
    }


def _norm(r: Dict[str, Any], market: str) -> Dict[str, Any]:
    return {
        "venue": "bybit",
        "market": market,
        "symbol": r.get("symbol"),
        "price": keep_num(r.get("lastPrice")),
        "change_pct": fraction_to_pct_str(r.get("price24hPcnt")),
        "quote_volume": keep_num(r.get("turnover24h")),
        "high": keep_num(r.get("highPrice24h")),
        "low": keep_num(r.get("lowPrice24h")),
        "bid": keep_num(r.get("bid1Price")),
        "ask": keep_num(r.get("ask1Price")),
    }


def _norm_fut(r: Dict[str, Any]) -> Dict[str, Any]:
    row = _norm(r, "futures")
    row["funding_rate"] = keep_num(r.get("fundingRate"))
    row["open_interest"] = keep_num(r.get("openInterest"))
    row["mark_price"] = keep_num(r.get("markPrice"))
    row["index_price"] = keep_num(r.get("indexPrice"))
    if row["open_interest"] is not None and row["mark_price"]:
        row["open_interest_usdt"] = dec_product_str(row["open_interest"], row["mark_price"])
    else:
        row["open_interest_usdt"] = row["open_interest"]
    return row


def _listings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in rows:
        if not isinstance(s, dict):
            continue
        launch = s.get("launchTime")
        if launch is None:
            continue
        try:
            ts = int(launch)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "venue": "bybit",
                "market": "spot",
                "symbol": s.get("symbol"),
                "base": s.get("baseCoin"),
                "quote": s.get("quoteCoin"),
                "listed_at_ms": ts,
            }
        )
    return out


def _sample_whales(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    prints: List[Dict[str, Any]] = []
    for sym in symbols:
        try:
            data = http.get_json(
                "https://api.bybit.com/v5/market/recent-trade",
                params={"category": "spot", "symbol": sym, "limit": 50},
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bybit whale soft-fail %s: %s", sym, redact_exc(exc, max_len=256)
            )
            continue
        for t in (data.get("result") or {}).get("list") or []:
            price = t.get("price")
            qty = t.get("size")
            if price is None or qty is None or price == "" or qty == "":
                continue
            prints.append(
                {
                    "venue": "bybit",
                    "symbol": sym,
                    "price": price,  # string → enrich → price_i / notional_i
                    "qty": qty,
                    "side": (t.get("side") or "").lower(),
                    "ts_ms": int(t.get("time") or 0),
                }
            )
    return prints
