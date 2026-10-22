from __future__ import annotations

import logging
import time
from typing import Any, Dict

from radar.config import load_config
from radar.http_client import DEFAULT_MAX_RESPONSE_BYTES
from radar.http_pool import acquire_http, stats_delta
from radar.ingest.kline_budget import budget_status, init_kline_budget
from radar.ingest.kline_skip import init_kline_skip
from radar.ingest.parse import abs_change_bps_sort_key, quote_volume_sort_key
from radar.ingest.sector_poll import group_poll_sec, sector_poll_sec
from radar.ingest.short_window import (
    ensure_universe_short_windows,
    select_short_window_sample,
)
from radar.ingest.universe import build_universe
from radar.native_bridge import native_status
from radar.score.engine import score_sectors
from radar.secrets_redact import redact_exc, redact_text

logger = logging.getLogger("radar.pipeline")

# Sector → ingest group (whales ride cex; cannot drive a separate HTTP path).
_SECTOR_GROUP = {
    "spot_alts": "cex",
    "futures": "cex",
    "listings": "cex",
    "whales": "cex",
    "memes": "memes",
    "stables": "stables",
}

# Honesty: ingest honors sectors.*.poll_sec via groups; MTF is sampled top-N only.
SECTOR_META = {
    "spot_alts": {
        "title": "Spot alts",
        "poll_hint": "group poll_sec · 24h + sampled KuCoin 15m/1h/4h + vol vs own 1h median (vol+|chg|)",
        "method": "24h/4h/1h/15m momentum × abs+relative volume − spread; excludes stables/memes",
    },
    "futures": {
        "title": "Futures (USDT-M)",
        "poll_hint": "group poll_sec · 24h + funding; OI + sampled KuCoin 15m/1h/4h (vol+|chg|)",
        "method": "OI/volume × MTF momentum; extreme funding flips side vs short-window bias",
    },
    "memes": {
        "title": "Memes (DEX + CEX keywords)",
        "poll_hint": "group poll_sec · DexScreener h24/h1/m5 + CEX keyword memes; sampled KuCoin 15m/1h/4h for CEX top",
        "method": "DEX liq/age + h24/h1/m5 blend; CEX MTF via ensure sample; thin liq flagged; DEX no extra candle GET",
    },
    "stables": {
        "title": "Stables",
        "poll_hint": "group poll_sec · CoinGecko peg",
        "method": "rank by |peg−1|; depeg_warn threshold",
    },
    "listings": {
        "title": "New listings",
        "poll_hint": "group poll_sec · exchange list metadata + live spot join",
        "method": "freshness + live book (ghosts skipped); cross-venue join ok; MTF via ensure sample",
    },
    "whales": {
        "title": "Whale prints",
        "poll_hint": "bundled with cex poll_sec · BTC/ETH/SOL size proxy + spot MTF join",
        "method": "notional ≥ threshold; spot short-window align/diverge flags; NOT smart money",
    },
}


def sector_meta_for(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Inject effective poll_sec / group cadence into SECTOR_META (honesty)."""
    out: Dict[str, Any] = {}
    for key, base in SECTOR_META.items():
        meta = dict(base)
        cfg_ps = sector_poll_sec(cfg, key)
        group = _SECTOR_GROUP[key]
        gps = group_poll_sec(cfg, group)
        meta["poll_sec"] = cfg_ps
        meta["group"] = group
        meta["group_poll_sec"] = gps
        detail = base["poll_hint"]
        if " · " in detail:
            detail = detail.split(" · ", 1)[1]
        if key == "whales":
            meta["poll_hint"] = f"bundled with cex {gps}s (cfg {cfg_ps}s) · {detail}"
        else:
            meta["poll_hint"] = f"group {group} {gps}s (cfg {cfg_ps}s) · {detail}"
        out[key] = meta
    return out


def _http_timeouts(cfg: Dict[str, Any]) -> tuple[int, int]:
    """Return (timeout_ms, cache_ttl_ms) — integer ms only."""
    if cfg.get("http_timeout_ms") is not None:
        timeout_ms = int(cfg["http_timeout_ms"])
    else:
        timeout_ms = int(round(float(cfg.get("http_timeout_sec") or 12) * 1000))
    if cfg.get("cache_ttl_ms") is not None:
        ttl_ms = int(cfg["cache_ttl_ms"])
    else:
        ttl_ms = int(round(float(cfg.get("cache_ttl_sec") or 25) * 1000))
    return timeout_ms, ttl_ms


def _ensure_fill_ratio(
    universe: Dict[str, Any], symbols: set[str], book: str
) -> float | None:
    """Fraction of symbols with 15m short-window on the live book.

    Empty ``symbols`` → ``None`` (N/A), never painted as 1.0. Callers must
    treat all-None as ensure failure (no scored targets).
    """
    if not symbols:
        return None
    by_sym = {
        str(r.get("symbol") or ""): r
        for r in (universe.get(book) or [])
        if isinstance(r, dict) and r.get("symbol")
    }
    ok = 0
    for sym in symbols:
        row = by_sym.get(sym)
        if row is None:
            continue
        if row.get("change_bps_15m") is not None or row.get("change_pct_15m") is not None:
            ok += 1
    return ok / float(len(symbols))


def _ensure_bucket_ok(ratio: float | None, *, threshold: float = 0.85) -> bool:
    """N/A (None) is OK for that bucket; numeric must meet threshold."""
    if ratio is None:
        return True
    return float(ratio) >= threshold


def _fmt_fill(ratio: float | None) -> str:
    return "n/a" if ratio is None else f"{float(ratio):.2f}"


def _cheap_book_syms(rows: list, *, n: int) -> set[str]:
    """Prelim targets via vol+|chg| sample — no full sector score (CPU/GIL)."""
    usdt = [
        r
        for r in (rows or [])
        if isinstance(r, dict) and str(r.get("symbol") or "").endswith("USDT")
    ]
    sample = select_short_window_sample(usdt, top_n=max(0, int(n)))
    return {str(r.get("symbol") or "") for r in sample if r.get("symbol")}


def _cheap_listing_syms(
    listings: list, *, n: int
) -> tuple[set[str], set[str]]:
    """Recent listings by age — split spot vs futures market tag."""
    rows = [r for r in (listings or []) if isinstance(r, dict) and r.get("symbol")]
    rows = sorted(
        rows,
        key=lambda r: int(r.get("listed_at") or r.get("age_hours") or 10**9),
    )
    spot: set[str] = set()
    fut: set[str] = set()
    for r in rows[: max(0, int(n))]:
        sym = str(r.get("symbol") or "")
        if not sym:
            continue
        if str(r.get("market") or "spot").lower() == "futures":
            fut.add(sym)
        else:
            spot.add(sym)
    return spot, fut


def _cheap_cex_meme_syms(universe: Dict[str, Any], cfg: Dict[str, Any], *, n: int) -> set[str]:
    """CEX keyword memes by volume — skip full memes score for prelim."""
    keywords = [str(k).upper() for k in (cfg.get("meme_keywords") or []) if k]
    if not keywords:
        return set()
    spot = [
        r
        for r in (universe.get("spot") or [])
        if isinstance(r, dict)
        and str(r.get("symbol") or "").endswith("USDT")
        and str(r.get("venue") or "").lower() in {"kucoin", "binance", "bybit", "mexc"}
    ]
    hits: list = []
    for r in spot:
        sym = str(r.get("symbol") or "").upper()
        base = sym[:-4] if sym.endswith("USDT") else sym
        if any(k in base for k in keywords):
            hits.append(r)
    hits.sort(key=quote_volume_sort_key, reverse=True)
    # Momentum fillers if volume thin
    if len(hits) < n:
        seen = {str(r.get("symbol") or "") for r in hits}
        for r in sorted(hits, key=abs_change_bps_sort_key, reverse=True):
            sym = str(r.get("symbol") or "")
            if sym and sym not in seen:
                hits.append(r)
                seen.add(sym)
            if len(hits) >= n:
                break
    return {str(r.get("symbol") or "") for r in hits[:n] if r.get("symbol")}


_MTF_PATCH_KEYS = (
    "change_bps_15m",
    "change_bps_1h",
    "change_bps_4h",
    "vol_vs_median_bps",
    "change_bps_5m",
)


def _cand_missing_15m(cand: Dict[str, Any]) -> bool:
    m = cand.get("metrics") if isinstance(cand.get("metrics"), dict) else {}
    return m.get("change_bps_15m") is None and cand.get("change_bps_15m") is None


def _display_missing_mtf_symbols(
    sectors: Dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Symbols on primary scored boards still lacking 15m MTF.

    Only ``spot_alts`` + ``futures`` (+ CEX memes) — listings/whales skipped so
    display fill cannot dominate scan wall-clock.
    """
    spot: set[str] = set()
    fut: set[str] = set()
    for cand in sectors.get("spot_alts") or []:
        if not isinstance(cand, dict) or not _cand_missing_15m(cand):
            continue
        sym = str(cand.get("symbol") or "")
        if sym:
            spot.add(sym)
    for cand in sectors.get("memes") or []:
        if not isinstance(cand, dict) or not _cand_missing_15m(cand):
            continue
        venue = str(cand.get("venue") or "").lower()
        src = str((cand.get("metrics") or {}).get("source") or "").lower()
        if src == "dex" or venue == "dexscreener":
            continue
        sym = str(cand.get("symbol") or "")
        if sym:
            spot.add(sym)
    for cand in sectors.get("futures") or []:
        if not isinstance(cand, dict) or not _cand_missing_15m(cand):
            continue
        sym = str(cand.get("symbol") or "")
        if sym:
            fut.add(sym)
    return spot, fut


def _patch_sectors_mtf_from_universe(
    sectors: Dict[str, Any], universe: Dict[str, Any]
) -> int:
    """Copy attached MTF fields from live books onto scored candidates."""
    spot_by = {
        str(r.get("symbol") or ""): r
        for r in (universe.get("spot") or [])
        if isinstance(r, dict) and r.get("symbol")
    }
    fut_by = {
        str(r.get("symbol") or ""): r
        for r in (universe.get("futures") or [])
        if isinstance(r, dict) and r.get("symbol")
    }
    patched = 0
    for rows in sectors.values():
        if not isinstance(rows, list):
            continue
        for cand in rows:
            if not isinstance(cand, dict):
                continue
            sym = str(cand.get("symbol") or "")
            if not sym:
                continue
            market = str(cand.get("market") or "spot").lower()
            src = fut_by.get(sym) if market == "futures" else spot_by.get(sym)
            if src is None:
                src = spot_by.get(sym) or fut_by.get(sym)
            if src is None:
                continue
            metrics = cand.get("metrics")
            if not isinstance(metrics, dict):
                metrics = {}
                cand["metrics"] = metrics
            changed = False
            for k in _MTF_PATCH_KEYS:
                if metrics.get(k) is None and src.get(k) is not None:
                    metrics[k] = src[k]
                    changed = True
            if changed:
                patched += 1
    return patched


def run_scan(
    cfg: Dict[str, Any] | None = None, *, force: bool = False
) -> Dict[str, Any]:
    cfg = cfg or load_config()
    allow = set(cfg.get("allowlist_hosts") or [])
    timeout_ms, ttl_ms = _http_timeouts(cfg)
    ingest = cfg.get("ingest") or {}
    max_resp = int(
        ingest.get("max_response_bytes")
        if ingest.get("max_response_bytes") is not None
        else DEFAULT_MAX_RESPONSE_BYTES
    )
    retries = int(ingest.get("http_retries") if ingest.get("http_retries") is not None else 2)
    backoff_ms = int(ingest.get("http_backoff_ms") or 200)
    # Process-lifetime client — TLS/cache reuse across scans (closed on app shutdown).
    http = acquire_http(
        allow,
        timeout_ms=timeout_ms,
        cache_ttl_ms=ttl_ms,
        retries=retries,
        backoff_ms=backoff_ms,
        max_response_bytes=max_resp,
    )
    stats_baseline = {k: int(v or 0) for k, v in (http.stats or {}).items()}
    t0_ms = int(time.time() * 1000)
    meta = sector_meta_for(cfg)
    ingest_ms = 0
    ensure_ms = 0
    t_ingest = int(time.time() * 1000)
    universe = build_universe(http, cfg, force=force)
    ingest_ms = int(time.time() * 1000) - t_ingest
    top_n = int(cfg.get("top_n") or 10)
    max_kline = ingest.get("max_kline_gets")
    max_kline_n = int(max_kline) if max_kline is not None else 96
    reuse_ttl = int(ingest.get("mtf_reuse_ttl_ms") or 0)
    init_kline_budget(http, max_kline_n)
    init_kline_skip(http)
    # Round 1 cheap prelim → ensure; round 2 only if scored top-N still missing.
    try:
        from radar.rank.topn import rank_top
        from radar.score.futures import score_futures
        from radar.score.listings import score_listings
        from radar.score.memes import score_memes
        from radar.score.spot_alts import score_spot_alts

        def _cex_meme_syms_from_rows(rows: list, n: int) -> set[str]:
            """CEX keyword memes only — DEX has no KuCoin candle path."""
            out: set[str] = set()
            for r in rows[: max(0, int(n) * 3)]:
                if not isinstance(r, dict):
                    continue
                m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
                src = str(m.get("source") or "").lower()
                venue = str(r.get("venue") or m.get("venue") or "").lower()
                if src == "cex" or venue in {"kucoin", "binance", "bybit", "mexc"}:
                    sym = str(r.get("symbol") or "")
                    if sym:
                        out.add(sym)
                    if len(out) >= n:
                        break
            return out

        def _merge_ensure(acc: Dict[str, Any], part: Dict[str, Any], rnd: int) -> None:
            for section in ("spot", "futures"):
                dst = acc.setdefault(section, {})
                for k, v in (part.get(section) or {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        dst[k] = int(dst.get(k) or 0) + int(v or 0)
            acc["spot_targets"] = int(part.get("spot_targets") or 0)
            acc["fut_targets"] = int(part.get("fut_targets") or 0)
            acc["rounds"] = rnd
            skipped = list(acc.get("waterfall_skipped") or [])
            for v in part.get("waterfall_skipped") or []:
                if v not in skipped:
                    skipped.append(v)
            acc["waterfall_skipped"] = skipped[:32]
            # Accumulate stage timers across rounds.
            sm = acc.setdefault("stage_ms", {})
            for k, v in (part.get("stage_ms") or {}).items():
                sm[k] = int(sm.get(k) or 0) + int(v or 0)
            acc["kline_budget"] = part.get("kline_budget") or budget_status(http)

        def _top_buckets() -> tuple[set[str], set[str], set[str], set[str], set[str]]:
            """Prefer native scorer for fill-check rank parity with final score."""
            try:
                from radar.native_bridge import native_available, score_universe_native

                if native_available() and bool(
                    (cfg.get("native") or {}).get("prefer", True)
                ):
                    sectors_n, _ = score_universe_native(universe, cfg, top_n)
                    spot_top = {
                        str(r.get("symbol") or "")
                        for r in (sectors_n.get("spot_alts") or [])
                        if isinstance(r, dict) and r.get("symbol")
                    }
                    fut_top = {
                        str(r.get("symbol") or "")
                        for r in (sectors_n.get("futures") or [])
                        if isinstance(r, dict) and r.get("symbol")
                    }
                    list_top = [
                        r
                        for r in (sectors_n.get("listings") or [])
                        if isinstance(r, dict) and r.get("symbol")
                    ]
                    list_spot = {
                        str(r.get("symbol") or "")
                        for r in list_top
                        if str(r.get("market") or "spot").lower() != "futures"
                    }
                    list_fut = {
                        str(r.get("symbol") or "")
                        for r in list_top
                        if str(r.get("market") or "spot").lower() == "futures"
                    }
                    meme_top = _cex_meme_syms_from_rows(
                        list(sectors_n.get("memes") or []), top_n
                    )
                    return spot_top, fut_top, list_spot, list_fut, meme_top
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "native top_buckets soft-fail — Python: %s",
                    redact_exc(exc, max_len=128),
                )
            spot_top = {
                str(r.get("symbol") or "")
                for r in rank_top(score_spot_alts(universe, cfg), top_n)
                if r.get("symbol")
            }
            fut_top = {
                str(r.get("symbol") or "")
                for r in rank_top(score_futures(universe, cfg), top_n)
                if r.get("symbol")
            }
            list_top = [
                r
                for r in rank_top(score_listings(universe, cfg), top_n)
                if r.get("symbol")
            ]
            list_spot = {
                str(r.get("symbol") or "")
                for r in list_top
                if str(r.get("market") or "spot").lower() != "futures"
            }
            list_fut = {
                str(r.get("symbol") or "")
                for r in list_top
                if str(r.get("market") or "spot").lower() == "futures"
            }
            meme_top = _cex_meme_syms_from_rows(
                rank_top(score_memes(universe, cfg), top_n), top_n
            )
            return spot_top, fut_top, list_spot, list_fut, meme_top

        def _fills_ok(
            spot_top: set[str],
            fut_top: set[str],
            list_spot: set[str],
            list_fut: set[str],
            meme_top: set[str],
        ) -> bool:
            return (
                _ensure_bucket_ok(_ensure_fill_ratio(universe, spot_top, "spot"))
                and _ensure_bucket_ok(_ensure_fill_ratio(universe, fut_top, "futures"))
                and _ensure_bucket_ok(_ensure_fill_ratio(universe, list_spot, "spot"))
                and _ensure_bucket_ok(_ensure_fill_ratio(universe, list_fut, "futures"))
                and _ensure_bucket_ok(_ensure_fill_ratio(universe, meme_top, "spot"))
                and bool(spot_top or fut_top or list_spot or list_fut or meme_top)
            )

        # Cap total fan-out; gate symbols always retained when trimming.
        ensure_cap = max(top_n * 3, 24)
        expand_n = max(8, top_n)
        ensure_acc: Dict[str, Any] = {}
        t_ensure = int(time.time() * 1000)

        # Round 1 — fill-gate symbols first (24h score), then small cheap expand.
        spot_top, fut_top, list_spot, list_fut, meme_top = _top_buckets()
        gate_spot = set(spot_top) | set(list_spot) | set(meme_top)
        gate_fut = set(fut_top) | set(list_fut)
        spot_syms = set(gate_spot)
        fut_syms = set(gate_fut)
        spot_syms |= _cheap_book_syms(universe.get("spot") or [], n=expand_n)
        fut_syms |= _cheap_book_syms(universe.get("futures") or [], n=expand_n)
        list_s, list_f = _cheap_listing_syms(
            universe.get("listings") or [], n=expand_n
        )
        spot_syms |= list_s
        fut_syms |= list_f
        spot_syms |= _cheap_cex_meme_syms(universe, cfg, n=expand_n)
        if len(spot_syms) > ensure_cap:
            extra = sorted(spot_syms - gate_spot)
            spot_syms = set(gate_spot) | set(
                extra[: max(0, ensure_cap - len(gate_spot))]
            )
        if len(fut_syms) > ensure_cap:
            extra = sorted(fut_syms - gate_fut)
            fut_syms = set(gate_fut) | set(
                extra[: max(0, ensure_cap - len(gate_fut))]
            )
        part = ensure_universe_short_windows(
            http,
            universe,
            spot_symbols=spot_syms,
            fut_symbols=fut_syms,
            reuse_ttl_ms=reuse_ttl,
        )
        _merge_ensure(ensure_acc, part, 1)

        # Freeze gate sets — re-ranking after partial MTF must not move the fill target.
        if not _fills_ok(spot_top, fut_top, list_spot, list_fut, meme_top):
            # Round 2 — same frozen gate (missing-only inside ensure).
            part2 = ensure_universe_short_windows(
                http,
                universe,
                spot_symbols=gate_spot,
                fut_symbols=gate_fut,
                reuse_ttl_ms=reuse_ttl,
            )
            _merge_ensure(ensure_acc, part2, 2)

        ensure_ms = int(time.time() * 1000) - t_ensure

        spot_fill = _ensure_fill_ratio(universe, spot_top, "spot")
        fut_fill = _ensure_fill_ratio(universe, fut_top, "futures")
        list_spot_fill = _ensure_fill_ratio(universe, list_spot, "spot")
        list_fut_fill = _ensure_fill_ratio(universe, list_fut, "futures")
        meme_fill = _ensure_fill_ratio(universe, meme_top, "spot")
        ensure_acc["spot_fill_ratio"] = (
            None if spot_fill is None else round(spot_fill, 4)
        )
        ensure_acc["fut_fill_ratio"] = (
            None if fut_fill is None else round(fut_fill, 4)
        )
        ensure_acc["list_spot_fill_ratio"] = (
            None if list_spot_fill is None else round(list_spot_fill, 4)
        )
        ensure_acc["list_fut_fill_ratio"] = (
            None if list_fut_fill is None else round(list_fut_fill, 4)
        )
        ensure_acc["meme_cex_fill_ratio"] = (
            None if meme_fill is None else round(meme_fill, 4)
        )
        ensure_acc["meme_cex_targets"] = len(meme_top)
        ensure_acc["ensure_ms"] = ensure_ms
        ensure_acc["kline_budget"] = budget_status(http)
        universe["short_window_ensure"] = ensure_acc
        any_targets = bool(spot_top or fut_top or list_spot or list_fut or meme_top)
        fills_ok = (
            _ensure_bucket_ok(spot_fill)
            and _ensure_bucket_ok(fut_fill)
            and _ensure_bucket_ok(list_spot_fill)
            and _ensure_bucket_ok(list_fut_fill)
            and _ensure_bucket_ok(meme_fill)
        )
        if any_targets and fills_ok:
            universe["mtf_ensure_ok"] = True
        else:
            universe["mtf_ensure_ok"] = False
            errs = list(universe.get("errors") or [])
            if not any_targets:
                errs.append("mtf_ensure_empty_targets")
            else:
                errs.append(
                    "mtf_ensure_incomplete:"
                    f"spot={_fmt_fill(spot_fill)},fut={_fmt_fill(fut_fill)},"
                    f"list_spot={_fmt_fill(list_spot_fill)},"
                    f"list_fut={_fmt_fill(list_fut_fill)},"
                    f"meme_cex={_fmt_fill(meme_fill)}"
                )
            universe["errors"] = errs[:32]
    except Exception as exc:  # noqa: BLE001
        ensure_ms = int(time.time() * 1000) - t0_ms - ingest_ms
        safe_ens = redact_exc(exc, max_len=128)
        logger.warning("short-window ensure soft-fail: %s", safe_ens)
        universe["short_window_ensure"] = {
            "error": safe_ens,
            "ensure_ms": ensure_ms,
            "kline_budget": budget_status(http),
        }
        universe["mtf_ensure_ok"] = False
        errs = list(universe.get("errors") or [])
        errs.append(f"mtf_ensure_failed:{safe_ens}")
        universe["errors"] = errs[:32]
    t_score = int(time.time() * 1000)
    try:
        sectors, backend = score_sectors(universe, cfg, top_n)
        # Display honesty: scored tops may diverge from frozen gate — fill MTF
        # for missing display rows from remaining kline budget, then patch metrics.
        kb_left = budget_status(http).get("kline_budget_left")
        if kb_left is None or int(kb_left) > 0:
            miss_spot, miss_fut = _display_missing_mtf_symbols(sectors)
            if miss_spot or miss_fut:
                t_disp = int(time.time() * 1000)
                try:
                    disp = ensure_universe_short_windows(
                        http,
                        universe,
                        spot_symbols=miss_spot or set(),
                        fut_symbols=miss_fut or set(),
                        reuse_ttl_ms=reuse_ttl,
                    )
                    acc = universe.get("short_window_ensure")
                    if isinstance(acc, dict):
                        acc["display_ensure_ms"] = int(time.time() * 1000) - t_disp
                        acc["display_spot_targets"] = len(miss_spot)
                        acc["display_fut_targets"] = len(miss_fut)
                        acc["display_patched"] = _patch_sectors_mtf_from_universe(
                            sectors, universe
                        )
                        acc["kline_budget"] = budget_status(http)
                        sm = acc.setdefault("stage_ms", {})
                        for k, v in (disp.get("stage_ms") or {}).items():
                            sm[k] = int(sm.get(k) or 0) + int(v or 0)
                    else:
                        _patch_sectors_mtf_from_universe(sectors, universe)
                except Exception as disp_exc:  # noqa: BLE001
                    logger.warning(
                        "display MTF ensure soft-fail: %s",
                        redact_exc(disp_exc, max_len=128),
                    )
        score_ms = int(time.time() * 1000) - t_score
        # Bound public error list — never dump unbounded venue noise
        errors = [
            redact_text(str(e))[:256]
            for e in (universe.get("errors") or [])[:32]
        ]
        ok = True
        mtf_ensure_ok = universe.get("mtf_ensure_ok")
        if mtf_ensure_ok is False:
            errors.append("mtf_ensure_failed")
            # Analysis may still score on 24h-only — mark degraded for auto/TG gates.
            ok = False
        if backend.startswith("python_") and (cfg.get("native") or {}).get("prefer"):
            errors.append(f"score_backend_degraded:{backend}")
            ok = False
    except Exception as exc:  # noqa: BLE001
        safe = redact_exc(exc, max_len=256)
        logger.error("score failed: %s", safe)
        return empty_snapshot(f"score failed: {safe}", cfg=cfg)

    # Per-scan delta on process-lifetime client (no double-count across scans).
    http_stats = stats_delta(http, stats_baseline)
    kb = budget_status(http)
    http_stats["kline_gets"] = int(kb.get("kline_gets") or 0)

    return {
        "ok": ok,
        "mtf_ensure_ok": universe.get("mtf_ensure_ok"),
        "schema_version": 2,
        "product": "DUP Market Radar",
        "disclaimer": "Watchlist · ручные ордера · авто ON/OFF (без фьючерсов). Не финансовый совет.",
        "generated_at": int(time.time()),
        "generated_at_ms": int(time.time() * 1000),
        "elapsed_ms": int(time.time() * 1000) - t0_ms,
        "elapsed_sec": (int(time.time() * 1000) - t0_ms) // 1000,
        "ingest_ms": ingest_ms,
        "ensure_ms": ensure_ms,
        "score_backend": backend,
        "score_ms": score_ms,
        "native": native_status(),
        "universe_size": universe.get("universe_size"),
        "http_stats": http_stats,
        "venue_ms": universe.get("venue_ms") or {},
        "circuit": universe.get("circuit") or {},
        "circuit_skipped": universe.get("circuit_skipped") or [],
        "poll": universe.get("poll") or {},
        "poll_skipped": universe.get("poll_skipped") or [],
        "due_groups": universe.get("due_groups") or [],
        "short_window_ensure": universe.get("short_window_ensure") or {},
        "dedupe": universe.get("dedupe") or {},
        "errors": errors,
        "sector_meta": meta,
        "sectors": sectors,
        "counts": {k: len(v) for k, v in sectors.items()},
    }


def empty_snapshot(
    reason: str, *, cfg: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    # Public API surface — never echo secrets from exception text
    safe_reason = redact_text(reason)[:256]
    meta = sector_meta_for(cfg) if cfg is not None else SECTOR_META
    return {
        "ok": False,
        "mtf_ensure_ok": False,
        "schema_version": 2,
        "product": "DUP Market Radar",
        "disclaimer": "Watchlist · ручные ордера · авто ON/OFF (без фьючерсов). Не финансовый совет.",
        "generated_at": int(time.time()),
        "generated_at_ms": int(time.time() * 1000),
        "errors": [safe_reason],
        "sector_meta": meta,
        "sectors": {k: [] for k in list(SECTOR_META) + ["futures_long", "futures_short"]},
        "counts": {},
        "universe_size": 0,
        "score_backend": "none",
        "http_stats": {},
        "poll": {},
        "poll_skipped": [],
        "due_groups": [],
        "dedupe": {},
        "native": native_status(),
    }
