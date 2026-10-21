"""KuCoin public ingest — primary venue for DUP Market Radar."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from radar.http_client import AllowlistHttp
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.kucoin")


def fetch_kucoin(http: AllowlistHttp) -> Dict[str, Any]:
    spot_raw = http.get_json("https://api.kucoin.com/api/v1/market/allTickers")
    tickers = (spot_raw.get("data") or {}).get("ticker") or []
    spot_rows = [_norm_spot(r) for r in tickers if isinstance(r, dict)]

    fut_rows: List[Dict[str, Any]] = []
    try:
        contracts = http.get_json("https://api-futures.kucoin.com/api/v1/contracts/active")
        data = contracts.get("data") or []
        if isinstance(data, list):
            fut_rows = [_norm_fut(r) for r in data if isinstance(r, dict) and r.get("status") == "Open"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("kucoin futures tickers soft-fail: %s", redact_exc(exc, max_len=256))
        fut_rows = []

    listings = _listings(http)
    whales = _sample_whales(http, ["BTC-USDT", "ETH-USDT", "SOL-USDT"])
    try:
        from radar.ingest.short_window import (
            attach_kucoin_futures_short_windows,
            attach_kucoin_short_windows,
        )

        sw = attach_kucoin_short_windows(http, spot_rows)
        if sw.get("n_15m") or sw.get("n_1h") or sw.get("n_4h") or sw.get("n_vol_vs"):
            logger.info(
                "kucoin spot short windows 15m=%s 1h=%s 4h=%s vol_vs=%s",
                sw.get("n_15m"),
                sw.get("n_1h"),
                sw.get("n_4h"),
                sw.get("n_vol_vs"),
            )
        fw = attach_kucoin_futures_short_windows(http, fut_rows)
        if fw.get("n_15m") or fw.get("n_1h") or fw.get("n_4h"):
            logger.info(
                "kucoin futures short windows 15m=%s 1h=%s 4h=%s",
                fw.get("n_15m"),
                fw.get("n_1h"),
                fw.get("n_4h"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kucoin short window soft-fail: %s", redact_exc(exc, max_len=256)
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


def _listings(http: AllowlistHttp) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        info = http.get_json("https://api.kucoin.com/api/v2/symbols")
    except Exception as exc:  # noqa: BLE001
        logger.warning("kucoin spot listings soft-fail: %s", redact_exc(exc, max_len=256))
        return out
    for s in info.get("data") or []:
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
    # Futures firstOpenDate as listing candidates
    try:
        contracts = http.get_json("https://api-futures.kucoin.com/api/v1/contracts/active")
        for r in contracts.get("data") or []:
            if not isinstance(r, dict) or r.get("status") != "Open":
                continue
            ts = r.get("firstOpenDate")
            if ts is None:
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
                    "listed_at_ms": int(ts),
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kucoin futures listings soft-fail: %s", redact_exc(exc, max_len=256)
        )
    return out


def _sample_whales(http: AllowlistHttp, symbols: List[str]) -> List[Dict[str, Any]]:
    prints: List[Dict[str, Any]] = []
    for sym in symbols:
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
            continue
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
