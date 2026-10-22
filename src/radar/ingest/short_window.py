"""Short-window change from KuCoin candles — sampled top-N, not full-universe N+1.

Spot (api.kucoin.com candles): [time, open, close, high, low, volume, amount]
  → change_pct_{15m,1h,4h} + vol_vs_median_bps

Futures (api-futures kline/query): [time, open, high, low, close, volume]
  → change_pct_{15m,1h,4h} only (volume is contracts, not quote)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from radar.http_client import AllowlistHttp
from radar.ingest.kline_budget import budget_consume, budget_status
from radar.ingest.kline_skip import (
    is_client_refuse_exc,
    kline_skip_add,
    kline_skip_has,
)
from radar.ingest.parse import abs_change_bps_sort_key, quote_volume_sort_key
from radar.money import mul_div, price_i, quote_i
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ingest.short_window")

DEFAULT_SHORT_SAMPLE_N = 24
# Futures kline fan-out capped lower — adds to spot GETs on the same scan.
DEFAULT_FUTURES_SHORT_SAMPLE_N = 16
# Not listed on Binance spot/fapi — skip to avoid 400 retry storms (Bybit/MEXC cover).
_SKIP_BINANCE_KLINE_VENUES = frozenset({"bybit", "mexc"})
# Per-venue waterfall stage cap — prevents prelim∪listings∪memes from burning
# the whole kline budget before scored top-N round 2.
_WATERFALL_STAGE_CAP = 24
# Parallel kline fan-out (AllowlistHttp lock no longer wraps network I/O).
# 12 (0.2.80; was 8) — primary+display share the same budgeted waterfall.
KLINE_PARALLEL_WORKERS = 12

_T = TypeVar("_T")
_R = TypeVar("_R")


def _parallel_map(
    items: List[_T], fn: Callable[[_T], _R], *, max_workers: int = KLINE_PARALLEL_WORKERS
) -> List[_R]:
    """Run fn over items; preserve input order. Sequential if ≤1 item."""
    if not items:
        return []
    workers = max(1, min(int(max_workers), len(items)))
    if workers == 1 or len(items) == 1:
        return [fn(x) for x in items]
    out: List[_R] = [None] * len(items)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def select_short_window_sample(
    rows: List[Dict[str, Any]], *, top_n: int
) -> List[Dict[str, Any]]:
    """Mix volume leaders + |24h change| leaders (same GET budget).

    Score top-N is often momentum-heavy while pure volume sample misses them —
    live evidence: spot_mtf coverage ≈1/10. Split ~2/3 vol + 1/3 momentum fillers.
    """
    n = max(0, min(64, int(top_n)))
    if n <= 0 or not rows:
        return []
    # Anchors often appear in watchlists / whales join; pin into sample.
    anchors = [
        r
        for r in rows
        if str(r.get("symbol") or "") in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    ]
    n_vol = max(1, n // 2)
    n_mom = max(0, n - n_vol)
    by_vol = sorted(rows, key=quote_volume_sort_key, reverse=True)
    vol_pick = by_vol[:n_vol]
    seen = {str(r.get("symbol") or "") for r in vol_pick}
    by_mom = sorted(rows, key=abs_change_bps_sort_key, reverse=True)
    mom_pick: List[Dict[str, Any]] = []
    for r in by_mom:
        sym = str(r.get("symbol") or "")
        if not sym or sym in seen:
            continue
        mom_pick.append(r)
        seen.add(sym)
        if len(mom_pick) >= n_mom:
            break
    out = vol_pick + mom_pick
    for a in anchors:
        sym = str(a.get("symbol") or "")
        if sym and sym not in seen and len(out) < min(64, n + 3):
            out.append(a)
            seen.add(sym)
    return out
_PERIOD_15M = 900
_PERIOD_1H = 3600
# ~5h lookback → enough closed 15m bars for a 4h span (16×900) + slack
_LOOKBACK_SEC = 18_000
_BUCKETS_1H = 4
_BUCKETS_4H = 16
# KuCoin spot candle: [time, open, close, high, low, volume, amount/turnover]
_CANDLE_AMOUNT_IDX = 6
# KuCoin futures kline: [time, open, high, low, close, volume]
_FUT_OPEN_IDX = 1
_FUT_CLOSE_IDX = 4


def candle_change_bps(open_s: Any, close_s: Any) -> Optional[int]:
    """(close - open) / open in bps — integer only."""
    o = price_i(open_s)
    c = price_i(close_s)
    if o is None or c is None or o <= 0:
        return None
    return mul_div(int(c) - int(o), 10_000, int(o))


def bps_to_pct_str(bps: int) -> str:
    """bps → percent string for enrich_market_row."""
    sign = "-" if bps < 0 else ""
    ab = abs(int(bps))
    return f"{sign}{ab // 100}.{ab % 100:02d}"


def candle_start_sec(raw_start: Any) -> Optional[int]:
    """Normalize candle open time to unix seconds (spot=sec, futures often=ms)."""
    try:
        start = int(raw_start)
    except (TypeError, ValueError):
        return None
    if start > 10_000_000_000_000:  # ns
        return start // 1_000_000
    if start > 10_000_000_000:  # ms
        return start // 1000
    return start


def _parse_candles(candles: List[Any]) -> List[Tuple[int, List[Any]]]:
    parsed: List[Tuple[int, List[Any]]] = []
    for raw in candles:
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            continue
        start = candle_start_sec(raw[0])
        if start is None:
            continue
        # Keep bucket payload; closed_* compares via parsed start_sec only.
        bucket = list(raw)
        bucket[0] = str(start)
        parsed.append((start, bucket))
    parsed.sort(key=lambda x: x[0])
    return parsed


def pick_closed_bucket(
    candles: List[Any],
    *,
    period_sec: int,
    now_sec: Optional[int] = None,
) -> Optional[List[Any]]:
    """Last fully closed bucket for period_sec; else newest well-formed."""
    if not isinstance(candles, list) or not candles:
        return None
    now = int(now_sec if now_sec is not None else time.time())
    period = max(60, int(period_sec))
    parsed = _parse_candles(candles)
    if not parsed:
        return None
    # Strictly closed: bucket end must be before now (not equal).
    closed = [b for start, b in parsed if start + period < now]
    if closed:
        return closed[-1]
    return parsed[-1][1]


def pick_1h_bucket(candles: List[Any], *, now_sec: Optional[int] = None) -> Optional[List[Any]]:
    """Back-compat: last closed 1h-shaped bucket (period 3600)."""
    return pick_closed_bucket(candles, period_sec=_PERIOD_1H, now_sec=now_sec)


def closed_buckets(
    candles: List[Any],
    *,
    period_sec: int,
    now_sec: Optional[int] = None,
) -> List[List[Any]]:
    now = int(now_sec if now_sec is not None else time.time())
    period = max(60, int(period_sec))
    return [b for start, b in _parse_candles(candles) if start + period < now]


def change_bps_from_closed_span(closed: List[List[Any]], *, n_buckets: int) -> Optional[int]:
    """Open of oldest of last n_buckets → close of newest."""
    if len(closed) < n_buckets or n_buckets < 1:
        return None
    span = closed[-n_buckets:]
    return candle_change_bps(span[0][1], span[-1][2])


def candle_quote_turnover_i(bucket: List[Any]) -> Optional[int]:
    """KuCoin candle amount (quote turnover) → quote micros."""
    if not isinstance(bucket, (list, tuple)) or len(bucket) <= _CANDLE_AMOUNT_IDX:
        return None
    return quote_i(bucket[_CANDLE_AMOUNT_IDX])


def _median_i(vals: List[int]) -> Optional[int]:
    if not vals:
        return None
    s = sorted(int(v) for v in vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) // 2


def vol_vs_median_bps_from_closed(
    closed: List[List[Any]], *, n_buckets: int = _BUCKETS_1H
) -> Optional[int]:
    """Last n_buckets quote turnover vs median of prior non-overlapping windows.

    Honesty: lookback is the same short candle window (~5h), not a 7d median.
    Returns (recent/baseline - 1) in bps, or None if sample too thin.
    """
    n = max(1, int(n_buckets))
    turns: List[int] = []
    for b in closed:
        t = candle_quote_turnover_i(b)
        if t is not None and t > 0:
            turns.append(int(t))
    # Need recent window + at least one prior comparable window
    if len(turns) < n * 2:
        return None
    recent = sum(turns[-n:])
    priors: List[int] = []
    i = len(turns) - n
    while i - n >= 0:
        i -= n
        priors.append(sum(turns[i : i + n]))
    baseline = _median_i(priors)
    if baseline is None or baseline <= 0:
        return None
    return mul_div(recent, 10_000, baseline) - 10_000


def _apply_kucoin_spot_buckets(
    row: Dict[str, Any],
    buckets: List[Any],
    *,
    now: int,
    counts: Dict[str, int],
) -> None:
    closed = closed_buckets(buckets, period_sec=_PERIOD_15M, now_sec=now)
    if not closed:
        bucket = pick_closed_bucket(buckets, period_sec=_PERIOD_15M, now_sec=now)
        if bucket:
            bps_15 = candle_change_bps(bucket[1], bucket[2])
            if bps_15 is not None:
                row["change_pct_15m"] = bps_to_pct_str(bps_15)
                counts["n_15m"] = int(counts.get("n_15m") or 0) + 1
        return
    bps_15 = candle_change_bps(closed[-1][1], closed[-1][2])
    if bps_15 is not None:
        row["change_pct_15m"] = bps_to_pct_str(bps_15)
        counts["n_15m"] = int(counts.get("n_15m") or 0) + 1
    bps_1h = change_bps_from_closed_span(closed, n_buckets=_BUCKETS_1H)
    if bps_1h is not None:
        row["change_pct_1h"] = bps_to_pct_str(bps_1h)
        counts["n_1h"] = int(counts.get("n_1h") or 0) + 1
    bps_4h = change_bps_from_closed_span(closed, n_buckets=_BUCKETS_4H)
    if bps_4h is not None:
        row["change_pct_4h"] = bps_to_pct_str(bps_4h)
        counts["n_4h"] = int(counts.get("n_4h") or 0) + 1
    vol_vs = vol_vs_median_bps_from_closed(closed, n_buckets=_BUCKETS_1H)
    if vol_vs is not None:
        row["vol_vs_median_bps"] = int(vol_vs)
        counts["n_vol_vs"] = int(counts.get("n_vol_vs") or 0) + 1


def attach_kucoin_short_windows(
    http: AllowlistHttp,
    spot_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Mutate top-N rows with change_pct_{15m,1h,4h} + vol_vs_median_bps. Returns counts."""
    n = max(0, min(64, int(top_n)))
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0, "n_vol_vs": 0}
    if n <= 0 or not spot_rows:
        return counts
    usdt = [
        r
        for r in spot_rows
        if isinstance(r, dict)
        and str(r.get("symbol") or "").endswith("USDT")
        and r.get("symbol_raw")
    ]
    sample = select_short_window_sample(usdt, top_n=n)
    now = int(time.time())

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        raw_sym = str(row.get("symbol_raw") or "")
        if not raw_sym:
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://api.kucoin.com/api/v1/market/candles",
                params={
                    "symbol": raw_sym,
                    "type": "15min",
                    "startAt": str(now - _LOOKBACK_SEC),
                    "endAt": str(now),
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin 15m candle soft-fail %s: %s",
                raw_sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        buckets = data.get("data") if isinstance(data, dict) else None
        return row, buckets if isinstance(buckets, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_kucoin_spot_buckets(row, buckets, now=now, counts=counts)
            _stamp_mtf(row)
    return counts


def attach_kucoin_1h_changes(
    http: AllowlistHttp,
    spot_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_SHORT_SAMPLE_N,
) -> int:
    """Back-compat wrapper — attaches 15m+1h via single 15min fan-out."""
    return int(attach_kucoin_short_windows(http, spot_rows, top_n=top_n).get("n_1h") or 0)


def kucoin_spot_symbol_raw(row: Dict[str, Any]) -> str:
    raw = row.get("symbol_raw")
    if raw:
        return str(raw)
    sym = str(row.get("symbol") or "").upper().replace("-", "").replace("_", "")
    if sym.endswith("USDT") and len(sym) > 4:
        return f"{sym[:-4]}-USDT"
    return ""


def kucoin_futures_symbol_raw(row: Dict[str, Any]) -> str:
    raw = row.get("symbol_raw")
    if raw:
        return str(raw)
    sym = str(row.get("symbol") or "").upper().replace("-", "").replace("_", "")
    if sym == "BTCUSDT":
        return "XBTUSDTM"
    if sym.endswith("USDT") and len(sym) > 4:
        return f"{sym}M"
    return ""


def mexc_futures_contract_path(row: Dict[str, Any]) -> str:
    """MEXC contract kline path symbol: BTC_USDT (not BTCUSDT / BTCUSDTM)."""
    sym = str(row.get("symbol") or "").upper().replace("-", "").replace("_", "")
    if sym.endswith("USDT") and len(sym) > 4:
        return f"{sym[:-4]}_USDT"
    return ""


def mexc_parallel_klines_to_ohlc(
    data: Dict[str, Any], *, cutoff_sec: Optional[int] = None
) -> List[List[Any]]:
    """MEXC contract kline payload → [[t,o,h,l,c], …] for _apply_ohlc_mtf."""
    times = data.get("time") or []
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    if not isinstance(times, list):
        return []
    n = min(
        len(times),
        len(opens) if isinstance(opens, list) else 0,
        len(highs) if isinstance(highs, list) else 0,
        len(lows) if isinstance(lows, list) else 0,
        len(closes) if isinstance(closes, list) else 0,
    )
    out: List[List[Any]] = []
    for i in range(n):
        t = candle_start_sec(times[i])
        if t is None:
            continue
        if cutoff_sec is not None and t < int(cutoff_sec):
            continue
        out.append([str(t), opens[i], highs[i], lows[i], closes[i]])
    return out


def _apply_pct_windows_to_bps(row: Dict[str, Any]) -> None:
    """After attach on already-enriched rows: pct → bps, drop float-ish pct keys."""
    from radar.money import pct_to_bps

    for pct_k, bps_k in (
        ("change_pct_15m", "change_bps_15m"),
        ("change_pct_1h", "change_bps_1h"),
        ("change_pct_4h", "change_bps_4h"),
    ):
        if row.get(bps_k) is None and row.get(pct_k) is not None:
            bps = pct_to_bps(row.get(pct_k))
            if bps is not None:
                row[bps_k] = int(bps)
        row.pop(pct_k, None)


def _attach_spot_rows(
    http: AllowlistHttp, rows: List[Dict[str, Any]]
) -> Dict[str, int]:
    """Attach candles to an explicit row list (already has symbol_raw)."""
    if not rows:
        return {"n_15m": 0, "n_1h": 0, "n_4h": 0, "n_vol_vs": 0}
    # Reuse sampler path by temporarily listing only these rows with raw set.
    return attach_kucoin_short_windows(http, rows, top_n=len(rows))


def _attach_fut_rows(
    http: AllowlistHttp, rows: List[Dict[str, Any]]
) -> Dict[str, int]:
    if not rows:
        return {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    return attach_kucoin_futures_short_windows(http, rows, top_n=len(rows))


def _missing_15m(row: Dict[str, Any], *, reuse_ttl_ms: int = 0) -> bool:
    """True if row needs a kline attach.

    When ``reuse_ttl_ms>0`` and MTF already present with a fresh stamp, skip
    re-fetch (cross-scan reuse). Untyped / unstamped rows with data are kept.
    """
    has = row.get("change_bps_15m") is not None or row.get("change_pct_15m") is not None
    if not has:
        return True
    ttl = max(0, int(reuse_ttl_ms or 0))
    if ttl <= 0:
        return False
    at = row.get("mtf_attached_at_ms")
    if at is None:
        return False
    try:
        age = int(time.time() * 1000) - int(at)
    except (TypeError, ValueError):
        return False
    return age >= ttl


def _stamp_mtf(row: Dict[str, Any]) -> None:
    row["mtf_attached_at_ms"] = int(time.time() * 1000)


def _target_fill_ratio(rows: List[Dict[str, Any]]) -> float:
    if not rows:
        return 1.0
    have = sum(
        1
        for r in rows
        if r.get("change_bps_15m") is not None or r.get("change_pct_15m") is not None
    )
    return have / float(len(rows))


def _usdt_missing_sample(
    rows: List[Dict[str, Any]], *, top_n: int, reuse_ttl_ms: int = 0
) -> List[Dict[str, Any]]:
    usdt = [
        r
        for r in rows
        if isinstance(r, dict)
        and str(r.get("symbol") or "").endswith("USDT")
        and _missing_15m(r, reuse_ttl_ms=reuse_ttl_ms)
    ]
    n = max(0, min(64, int(top_n)))
    if n <= 0:
        return []
    return select_short_window_sample(usdt, top_n=n) if len(usdt) > n else usdt[:n]


# Stop venue waterfall when target set already meets fill honesty (same as pipeline).
_ENSURE_FILL_OK = 0.85


def _quote_vol_vs_median_bps(
    closed: List[List[Any]], *, quote_idx: int
) -> Optional[int]:
    """Recent 1h quote turnover vs median of prior 1h windows (same lookback)."""
    turns: List[int] = []
    need = quote_idx + 1
    for b in closed:
        if not isinstance(b, (list, tuple)) or len(b) < need:
            continue
        t = quote_i(b[quote_idx])
        if t is not None and t > 0:
            turns.append(int(t))
    n = _BUCKETS_1H
    if len(turns) < n * 2:
        return None
    recent = sum(turns[-n:])
    priors: List[int] = []
    i = len(turns) - n
    while i - n >= 0:
        i -= n
        priors.append(sum(turns[i : i + n]))
    baseline = _median_i(priors)
    if baseline is None or baseline <= 0:
        return None
    return mul_div(recent, 10_000, baseline) - 10_000


def _apply_ohlc_mtf(
    row: Dict[str, Any],
    buckets: List[Any],
    *,
    now: int,
    counts: Dict[str, int],
    quote_vol_idx: Optional[int] = None,
) -> None:
    """Mutate row from OHLC candles [t,o,h,l,c,…]. Optional quote turnover index."""
    if not isinstance(buckets, list) or not buckets:
        return
    closed = closed_buckets(buckets, period_sec=_PERIOD_15M, now_sec=now)
    if not closed:
        bucket = pick_closed_bucket(buckets, period_sec=_PERIOD_15M, now_sec=now)
        if bucket and len(bucket) > _FUT_CLOSE_IDX:
            bps_15 = candle_change_bps(bucket[_FUT_OPEN_IDX], bucket[_FUT_CLOSE_IDX])
            if bps_15 is not None:
                row["change_pct_15m"] = bps_to_pct_str(bps_15)
                counts["n_15m"] = int(counts.get("n_15m") or 0) + 1
        return
    last = closed[-1]
    if len(last) > _FUT_CLOSE_IDX:
        bps_15 = candle_change_bps(last[_FUT_OPEN_IDX], last[_FUT_CLOSE_IDX])
        if bps_15 is not None:
            row["change_pct_15m"] = bps_to_pct_str(bps_15)
            counts["n_15m"] = int(counts.get("n_15m") or 0) + 1
    bps_1h = change_bps_from_closed_span_ohlc(
        closed,
        n_buckets=_BUCKETS_1H,
        open_idx=_FUT_OPEN_IDX,
        close_idx=_FUT_CLOSE_IDX,
    )
    if bps_1h is not None:
        row["change_pct_1h"] = bps_to_pct_str(bps_1h)
        counts["n_1h"] = int(counts.get("n_1h") or 0) + 1
    bps_4h = change_bps_from_closed_span_ohlc(
        closed,
        n_buckets=_BUCKETS_4H,
        open_idx=_FUT_OPEN_IDX,
        close_idx=_FUT_CLOSE_IDX,
    )
    if bps_4h is not None:
        row["change_pct_4h"] = bps_to_pct_str(bps_4h)
        counts["n_4h"] = int(counts.get("n_4h") or 0) + 1
    if quote_vol_idx is not None and "n_vol_vs" in counts:
        vol_vs = _quote_vol_vs_median_bps(closed, quote_idx=quote_vol_idx)
        if vol_vs is not None:
            row["vol_vs_median_bps"] = int(vol_vs)
            counts["n_vol_vs"] = int(counts.get("n_vol_vs") or 0) + 1


def _binance_vol_vs_median_bps(closed: List[List[Any]]) -> Optional[int]:
    """Binance kline: index 7 = quote asset volume (back-compat for tests)."""
    return _quote_vol_vs_median_bps(closed, quote_idx=7)


def attach_binance_spot_short_windows(
    http: AllowlistHttp,
    spot_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Fallback MTF via Binance /api/v3/klines (OHLC layout like futures).

    Used when KuCoin candles miss — common for Binance-only alts in score top.
    Skip bybit/mexc rows (not on Binance → 400 spam); Bybit path covers bybit.
    """
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0, "n_vol_vs": 0}
    scoped = [
        r
        for r in spot_rows
        if isinstance(r, dict)
        and str(r.get("venue") or "") not in _SKIP_BINANCE_KLINE_VENUES
    ]
    sample = _usdt_missing_sample(scoped, top_n=top_n)
    if not sample:
        return counts
    now = int(time.time())
    now_ms = now * 1000
    from_ms = now_ms - (_LOOKBACK_SEC * 1000)

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        sym = str(row.get("symbol") or "")
        if not sym:
            return row, None
        if kline_skip_has(http, "binance_spot", sym):
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": sym,
                    "interval": "15m",
                    "startTime": from_ms,
                    "endTime": now_ms,
                    "limit": 100,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            if is_client_refuse_exc(exc):
                kline_skip_add(http, "binance_spot", sym)
            logger.warning(
                "binance 15m kline soft-fail %s: %s",
                sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        return row, data if isinstance(data, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts, quote_vol_idx=7)
            _stamp_mtf(row)
    return counts


def attach_bybit_spot_short_windows(
    http: AllowlistHttp,
    spot_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Fallback MTF via Bybit v5 spot kline — Bybit-only alts (e.g. BSBUSDT)."""
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0, "n_vol_vs": 0}
    sample = _usdt_missing_sample(spot_rows, top_n=top_n)
    if not sample:
        return counts
    now = int(time.time())
    now_ms = now * 1000
    from_ms = now_ms - (_LOOKBACK_SEC * 1000)

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        sym = str(row.get("symbol") or "")
        if not sym:
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://api.bybit.com/v5/market/kline",
                params={
                    "category": "spot",
                    "symbol": sym,
                    "interval": "15",
                    "start": from_ms,
                    "end": now_ms,
                    "limit": 100,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bybit spot 15m kline soft-fail %s: %s",
                sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        if not isinstance(data, dict) or int(data.get("retCode") or 0) != 0:
            return row, None
        buckets = (data.get("result") or {}).get("list")
        return row, buckets if isinstance(buckets, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts, quote_vol_idx=6)
            _stamp_mtf(row)
    return counts


def attach_binance_futures_short_windows(
    http: AllowlistHttp,
    fut_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_FUTURES_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Fallback MTF via Binance USDT-M /fapi/v1/klines when KuCoin futures miss."""
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    scoped = [
        r
        for r in fut_rows
        if isinstance(r, dict)
        and str(r.get("venue") or "") not in _SKIP_BINANCE_KLINE_VENUES
    ]
    sample = _usdt_missing_sample(scoped, top_n=top_n)
    if not sample:
        return counts
    now = int(time.time())
    now_ms = now * 1000
    from_ms = now_ms - (_LOOKBACK_SEC * 1000)

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        sym = str(row.get("symbol") or "")
        if not sym:
            return row, None
        if kline_skip_has(http, "binance_fut", sym):
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://fapi.binance.com/fapi/v1/klines",
                params={
                    "symbol": sym,
                    "interval": "15m",
                    "startTime": from_ms,
                    "endTime": now_ms,
                    "limit": 100,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            if is_client_refuse_exc(exc):
                kline_skip_add(http, "binance_fut", sym)
            logger.warning(
                "binance fut 15m kline soft-fail %s: %s",
                sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        return row, data if isinstance(data, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts)
            _stamp_mtf(row)
    return counts


def attach_bybit_futures_short_windows(
    http: AllowlistHttp,
    fut_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_FUTURES_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Fallback MTF via Bybit v5 linear kline."""
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    sample = _usdt_missing_sample(fut_rows, top_n=top_n)
    if not sample:
        return counts
    now = int(time.time())
    now_ms = now * 1000
    from_ms = now_ms - (_LOOKBACK_SEC * 1000)

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        sym = str(row.get("symbol") or "")
        if not sym:
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://api.bybit.com/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": sym,
                    "interval": "15",
                    "start": from_ms,
                    "end": now_ms,
                    "limit": 100,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bybit fut 15m kline soft-fail %s: %s",
                sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        if not isinstance(data, dict) or int(data.get("retCode") or 0) != 0:
            return row, None
        buckets = (data.get("result") or {}).get("list")
        return row, buckets if isinstance(buckets, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts)
            _stamp_mtf(row)
    return counts


def attach_mexc_futures_short_windows(
    http: AllowlistHttp,
    fut_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_FUTURES_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Fallback MTF via MEXC contract kline (parallel arrays → OHLC)."""
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    sample = _usdt_missing_sample(fut_rows, top_n=top_n)
    if not sample:
        return counts
    now = int(time.time())
    start_sec = now - _LOOKBACK_SEC

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        path_sym = mexc_futures_contract_path(row)
        if not path_sym:
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                f"https://contract.mexc.com/api/v1/contract/kline/{path_sym}",
                params={
                    "interval": "Min15",
                    "start": start_sec,
                    "end": now,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mexc fut 15m kline soft-fail %s: %s",
                path_sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        if not isinstance(data, dict) or data.get("success") is False:
            return row, None
        payload = data.get("data")
        if not isinstance(payload, dict):
            return row, None
        return row, mexc_parallel_klines_to_ohlc(payload, cutoff_sec=start_sec) or None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts)
            _stamp_mtf(row)
    return counts


def _merge_window_counts(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = int(dst.get(k) or 0) + int(v or 0)


def ensure_universe_short_windows(
    http: AllowlistHttp,
    universe: Dict[str, Any],
    *,
    spot_symbols: Optional[set[str]] = None,
    fut_symbols: Optional[set[str]] = None,
    spot_top_n: int = DEFAULT_SHORT_SAMPLE_N,
    fut_top_n: int = DEFAULT_FUTURES_SHORT_SAMPLE_N,
    reuse_ttl_ms: int = 0,
    fill_threshold: float = _ENSURE_FILL_OK,
) -> Dict[str, Any]:
    """Fill MTF on merged rows missing windows.

    If spot_symbols/fut_symbols given — target those (post prelim).
    Spot and futures waterfalls run in parallel (shared atomic kline budget).
    Short-circuits venue stages when target fill ≥ fill_threshold.
    Process-lifetime MTF cache hydrates rows before the waterfall (0.2.87).
    """
    from radar.ingest.mtf_cache import mtf_cache

    thr = float(fill_threshold)
    ttl = max(0, int(reuse_ttl_ms or 0))
    cache = mtf_cache()
    spot_all = [r for r in (universe.get("spot") or []) if isinstance(r, dict)]
    fut_all = [r for r in (universe.get("futures") or []) if isinstance(r, dict)]
    hyd_spot = cache.hydrate_rows(spot_all, market="spot", ttl_ms=ttl)
    hyd_fut = cache.hydrate_rows(fut_all, market="futures", ttl_ms=ttl)

    def _run_spot() -> Tuple[Dict[str, int], List[Dict[str, Any]], List[str], Dict[str, int]]:
        skipped: List[str] = []
        stage: Dict[str, int] = {}

        def _timed(label: str, fn: Callable[[], Any]) -> Any:
            t0 = int(time.time() * 1000)
            try:
                return fn()
            finally:
                stage[label] = int(stage.get(label) or 0) + (
                    int(time.time() * 1000) - t0
                )

        spot = spot_all
        need = [r for r in spot if _missing_15m(r, reuse_ttl_ms=ttl)]
        if spot_symbols is not None:
            need = [r for r in need if str(r.get("symbol") or "") in spot_symbols]
        for r in need:
            derived = kucoin_spot_symbol_raw(r)
            if derived and not r.get("symbol_raw"):
                r["symbol_raw"] = derived
        kucoin_ready = [r for r in need if r.get("symbol_raw")]

        stage_cap = max(int(spot_top_n), _WATERFALL_STAGE_CAP)

        def _kc() -> Dict[str, int]:
            if spot_symbols is None:
                return attach_kucoin_short_windows(http, kucoin_ready, top_n=spot_top_n)
            return _attach_spot_rows(http, kucoin_ready[:stage_cap])

        sw = _timed("spot_kucoin_ms", _kc)
        for r in need:
            _apply_pct_windows_to_bps(r)

        def _ok() -> bool:
            return _target_fill_ratio(need) >= thr if need else True

        if not _ok():
            still = [r for r in need if _missing_15m(r, reuse_ttl_ms=0)]
            bw = _timed(
                "spot_binance_ms",
                lambda: attach_binance_spot_short_windows(
                    http, still, top_n=min(len(still), stage_cap)
                ),
            )
            for r in still:
                _apply_pct_windows_to_bps(r)
            _merge_window_counts(sw, bw)
            sw["n_binance"] = int(bw.get("n_15m") or 0)
        else:
            skipped.append("binance_spot")
            sw["n_binance"] = 0

        if not _ok():
            still = [r for r in need if _missing_15m(r, reuse_ttl_ms=0)]
            yw = _timed(
                "spot_bybit_ms",
                lambda: attach_bybit_spot_short_windows(
                    http, still, top_n=min(len(still), stage_cap)
                ),
            )
            for r in still:
                _apply_pct_windows_to_bps(r)
            _merge_window_counts(sw, yw)
            sw["n_bybit"] = int(yw.get("n_15m") or 0)
        else:
            skipped.append("bybit_spot")
            sw.setdefault("n_bybit", 0)
        return sw, need, skipped, stage

    def _run_fut() -> Tuple[Dict[str, int], List[Dict[str, Any]], List[str], Dict[str, int]]:
        skipped: List[str] = []
        stage: Dict[str, int] = {}

        def _timed(label: str, fn: Callable[[], Any]) -> Any:
            t0 = int(time.time() * 1000)
            try:
                return fn()
            finally:
                stage[label] = int(stage.get(label) or 0) + (
                    int(time.time() * 1000) - t0
                )

        fut = fut_all
        need = [r for r in fut if _missing_15m(r, reuse_ttl_ms=ttl)]
        if fut_symbols is not None:
            need = [r for r in need if str(r.get("symbol") or "") in fut_symbols]
        for r in need:
            if str(r.get("venue") or "") != "kucoin":
                continue
            derived = kucoin_futures_symbol_raw(r)
            if derived and not r.get("symbol_raw"):
                r["symbol_raw"] = derived
        kucoin_fut = [
            r
            for r in need
            if r.get("symbol_raw") and str(r.get("venue") or "") == "kucoin"
        ]

        stage_cap = max(int(fut_top_n), _WATERFALL_STAGE_CAP)

        def _kc() -> Dict[str, int]:
            if fut_symbols is None:
                return attach_kucoin_futures_short_windows(
                    http, kucoin_fut, top_n=fut_top_n
                )
            return _attach_fut_rows(http, kucoin_fut[:stage_cap])

        fw = _timed("fut_kucoin_ms", _kc)
        for r in need:
            _apply_pct_windows_to_bps(r)

        def _ok() -> bool:
            return _target_fill_ratio(need) >= thr if need else True

        if not _ok():
            still_f = [r for r in need if _missing_15m(r, reuse_ttl_ms=0)]
            bfw = _timed(
                "fut_binance_ms",
                lambda: attach_binance_futures_short_windows(
                    http, still_f, top_n=min(len(still_f), stage_cap)
                ),
            )
            for r in still_f:
                _apply_pct_windows_to_bps(r)
            _merge_window_counts(fw, bfw)
            fw["n_binance"] = int(bfw.get("n_15m") or 0)
        else:
            skipped.append("binance_fut")
            fw["n_binance"] = 0

        if not _ok():
            still_f = [r for r in need if _missing_15m(r, reuse_ttl_ms=0)]
            yfw = _timed(
                "fut_bybit_ms",
                lambda: attach_bybit_futures_short_windows(
                    http, still_f, top_n=min(len(still_f), stage_cap)
                ),
            )
            for r in still_f:
                _apply_pct_windows_to_bps(r)
            _merge_window_counts(fw, yfw)
            fw["n_bybit"] = int(yfw.get("n_15m") or 0)
        else:
            skipped.append("bybit_fut")
            fw.setdefault("n_bybit", 0)

        if not _ok():
            still_f = [r for r in need if _missing_15m(r, reuse_ttl_ms=0)]
            mfw = _timed(
                "fut_mexc_ms",
                lambda: attach_mexc_futures_short_windows(
                    http, still_f, top_n=min(len(still_f), stage_cap)
                ),
            )
            for r in still_f:
                _apply_pct_windows_to_bps(r)
            _merge_window_counts(fw, mfw)
            fw["n_mexc"] = int(mfw.get("n_15m") or 0)
        else:
            skipped.append("mexc_fut")
            fw.setdefault("n_mexc", 0)
        return fw, need, skipped, stage

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_spot = pool.submit(_run_spot)
        fut_fut = pool.submit(_run_fut)
        sw, need_spot, skip_s, stage_s = fut_spot.result()
        fw, need_fut, skip_f, stage_f = fut_fut.result()

    stage_ms = {**stage_s, **stage_f}
    skipped_venues = skip_s + skip_f
    rem_spot = cache.remember_rows(spot_all, market="spot")
    rem_fut = cache.remember_rows(fut_all, market="futures")
    out = {
        "spot": sw,
        "futures": fw,
        "spot_targets": len(need_spot),
        "fut_targets": len(need_fut),
        "spot_fill_ratio": round(_target_fill_ratio(need_spot), 4) if need_spot else None,
        "fut_fill_ratio": round(_target_fill_ratio(need_fut), 4) if need_fut else None,
        "waterfall_skipped": skipped_venues[:16],
        "stage_ms": stage_ms,
        "books_parallel": True,
        "kline_budget": budget_status(http),
        "mtf_cache_hydrate_spot": int(hyd_spot),
        "mtf_cache_hydrate_fut": int(hyd_fut),
        "mtf_cache_remember_spot": int(rem_spot),
        "mtf_cache_remember_fut": int(rem_fut),
    }
    universe["short_window_ensure"] = out
    return out


def change_bps_from_closed_span_ohlc(
    closed: List[List[Any]],
    *,
    n_buckets: int,
    open_idx: int,
    close_idx: int,
) -> Optional[int]:
    if len(closed) < n_buckets or n_buckets < 1:
        return None
    span = closed[-n_buckets:]
    if len(span[0]) <= max(open_idx, close_idx) or len(span[-1]) <= max(open_idx, close_idx):
        return None
    return candle_change_bps(span[0][open_idx], span[-1][close_idx])


def attach_kucoin_futures_short_windows(
    http: AllowlistHttp,
    fut_rows: List[Dict[str, Any]],
    *,
    top_n: int = DEFAULT_FUTURES_SHORT_SAMPLE_N,
) -> Dict[str, int]:
    """Mutate top-N futures rows with change_pct_{15m,1h,4h} from futures klines."""
    n = max(0, min(64, int(top_n)))
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    if n <= 0 or not fut_rows:
        return counts
    usdt = [
        r
        for r in fut_rows
        if isinstance(r, dict)
        and str(r.get("symbol") or "").endswith("USDT")
        and r.get("symbol_raw")
    ]
    sample = select_short_window_sample(usdt, top_n=n)
    now = int(time.time())
    # KuCoin futures kline from/to are milliseconds (spot candles use seconds).
    now_ms = now * 1000
    from_ms = now_ms - (_LOOKBACK_SEC * 1000)

    def _fetch(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[List[Any]]]:
        raw_sym = str(row.get("symbol_raw") or "")
        if not raw_sym:
            return row, None
        if not budget_consume(http):
            return row, None
        try:
            data = http.get_json(
                "https://api-futures.kucoin.com/api/v1/kline/query",
                params={
                    "symbol": raw_sym,
                    "granularity": 15,
                    "from": from_ms,
                    "to": now_ms,
                },
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kucoin futures 15m kline soft-fail %s: %s",
                raw_sym[:32],
                redact_exc(exc, max_len=128),
            )
            return row, None
        buckets = data.get("data") if isinstance(data, dict) else None
        return row, buckets if isinstance(buckets, list) else None

    for row, buckets in _parallel_map(sample, _fetch):
        if buckets:
            _apply_ohlc_mtf(row, buckets, now=now, counts=counts)
            _stamp_mtf(row)
    return counts
