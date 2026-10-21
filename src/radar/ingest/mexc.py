from __future__ import annotations

from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.ingest.parse import fraction_to_pct_str, keep_num


def fetch_mexc(http: AllowlistHttp) -> Dict[str, Any]:
    spot = http.get_json("https://api.mexc.com/api/v3/ticker/24hr")
    spot_rows = [_norm_spot(r) for r in spot if isinstance(r, dict)]

    fut_rows: List[Dict[str, Any]] = []
    try:
        contracts = http.get_json("https://contract.mexc.com/api/v1/contract/ticker")
        data = contracts.get("data") if isinstance(contracts, dict) else None
        if isinstance(data, list):
            fut_rows = [_norm_fut(r) for r in data if isinstance(r, dict)]
    except Exception:  # noqa: BLE001
        fut_rows = []

    listings: List[Dict[str, Any]] = []
    try:
        info = http.get_json("https://api.mexc.com/api/v3/exchangeInfo")
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
    except Exception:  # noqa: BLE001
        listings = []

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
