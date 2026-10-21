from __future__ import annotations

from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.ingest.parse import keep_num

# CoinGecko ids for common stables
_STABLE_IDS = {
    "tether": "USDT",
    "usd-coin": "USDC",
    "dai": "DAI",
    "first-digital-usd": "FDUSD",
    "true-usd": "TUSD",
    "ethena-usde": "USDE",
    "paypal-usd": "PYUSD",
}


def fetch_stable_prices(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    ids = ",".join(_STABLE_IDS.keys())
    data = http.get_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
        },
    )
    want = {s.upper() for s in symbols}
    out: List[Dict[str, Any]] = []
    for cg_id, ticker in _STABLE_IDS.items():
        if want and ticker not in want:
            continue
        row = data.get(cg_id) or {}
        # peg_deviation_bps computed in enrich from price_i (market=stable)
        out.append(
            {
                "venue": "coingecko",
                "market": "stable",
                "symbol": ticker,
                "price": keep_num(row.get("usd")),
                "quote_volume": keep_num(row.get("usd_24h_vol")),
                "change_pct": keep_num(row.get("usd_24h_change")),
            }
        )
    return out
