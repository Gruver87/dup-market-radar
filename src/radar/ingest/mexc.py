from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from radar.http_client import AllowlistHttp
from radar.ingest.parse import fraction_to_pct_str, keep_num
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.mexc")


def _bootstrap_mexc(
    http: AllowlistHttp,
) -> Tuple[Any, Any, Any]:
    """Parallel spot 24hr ∥ contract ticker ∥ exchangeInfo."""

    def _spot() -> Any:
        return http.get_json("https://api.mexc.com/api/v3/ticker/24hr")

    def _fut() -> Any:
        return http.get_json("https://contract.mexc.com/api/v1/contract/ticker")

    def _info() -> Any:
        return http.get_json("https://api.mexc.com/api/v3/exchangeInfo")

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_s = pool.submit(_spot)
        f_f = pool.submit(_fut)
        f_i = pool.submit(_info)
        spot = f_s.result()
        try:
            fut = f_f.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mexc futures soft-fail: %s", redact_exc(exc, max_len=128))
            fut = None
        try:
            info = f_i.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mexc exchangeInfo soft-fail: %s", redact_exc(exc, max_len=128))
            info = None
        return spot, fut, info


def fetch_mexc(http: AllowlistHttp) -> Dict[str, Any]:
    spot, contracts, info = _bootstrap_mexc(http)
    spot_list = spot if isinstance(spot, list) else []
    spot_rows = [_norm_spot(r) for r in spot_list if isinstance(r, dict)]

    fut_rows: List[Dict[str, Any]] = []
    if isinstance(contracts, dict):
        data = contracts.get("data")
        if isinstance(data, list):
            fut_rows = [_norm_fut(r) for r in data if isinstance(r, dict)]

    listings: List[Dict[str, Any]] = []
    if isinstance(info, dict):
        for s in info.get("symbols") or []:
            if not isinstance(s, dict):
                continue
            st = str(s.get("status", "")).upper()
            if st not in {"1", "ENABLED", "TRADING"}:
                continue
            listings.append(
                {
                    "venue": "mexc",
                    "market": "spot",
                    "symbol": s.get("symbol"),
                    "base": s.get("baseAsset"),
                    "quote": s.get("quoteAsset"),
                    "listed_at_ms": None,
                }
            )

    return {
        "venue": "mexc",
        "spot": spot_rows,
        "futures": fut_rows,
        "listings": listings,
        "whales": [],
    }


def _norm_spot(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "venue": "mexc",
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
    sym = r.get("symbol") or r.get("contractName")
    # riseFallRate is typically a fraction on MEXC contracts
    return {
        "venue": "mexc",
        "market": "futures",
        "symbol": str(sym).replace("_", "") if sym else None,
        "price": keep_num(r.get("lastPrice") or r.get("fairPrice")),
        "change_pct": fraction_to_pct_str(r.get("riseFallRate")),
        "quote_volume": keep_num(r.get("amount24") or r.get("volume24")),
        "funding_rate": keep_num(r.get("fundingRate")),
        "open_interest": keep_num(r.get("holdVol")),
        "open_interest_usdt": keep_num(r.get("holdVol")),
        "mark_price": keep_num(r.get("fairPrice")),
        "index_price": keep_num(r.get("indexPrice")),
    }
