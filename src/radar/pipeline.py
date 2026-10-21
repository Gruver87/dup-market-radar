from __future__ import annotations

import logging
import time
from typing import Any, Dict

from radar.config import load_config
from radar.http_client import DEFAULT_MAX_RESPONSE_BYTES, AllowlistHttp
from radar.ingest.sector_poll import group_poll_sec, sector_poll_sec
from radar.ingest.short_window import ensure_universe_short_windows
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
    http = AllowlistHttp(
        allow,
        timeout_ms=timeout_ms,
        cache_ttl_ms=ttl_ms,
        max_response_bytes=max_resp,
    )
    t0_ms = int(time.time() * 1000)
    meta = sector_meta_for(cfg)
    try:
        universe = build_universe(http, cfg, force=force)
        top_n = int(cfg.get("top_n") or 10)
        # Two rounds: fill MTF for prelim top → scores reshuffle → fill again.
        try:
            from radar.rank.topn import rank_top
            from radar.score.futures import score_futures
            from radar.score.listings import score_listings
            from radar.score.memes import score_memes
            from radar.score.spot_alts import score_spot_alts

            def _cex_meme_syms(n: int) -> set[str]:
                """CEX keyword memes only — DEX has no KuCoin candle path."""
                out: set[str] = set()
                for r in rank_top(score_memes(universe, cfg), n):
                    if not isinstance(r, dict):
                        continue
                    m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
                    src = str(m.get("source") or "").lower()
                    venue = str(r.get("venue") or m.get("venue") or "").lower()
                    if src == "cex" or venue in {"kucoin", "binance", "bybit", "mexc"}:
                        sym = str(r.get("symbol") or "")
                        if sym:
                            out.add(sym)
                return out

            # Smaller prelim + adaptive 2nd round (profile: ensure was ~28s of ~42s).
            prelim_n = max(top_n * 2, 16)
            ensure_acc: Dict[str, Any] = {}
            for rnd in range(2):
                spot_syms = {
                    str(r.get("symbol") or "")
                    for r in rank_top(score_spot_alts(universe, cfg), prelim_n)
                }
                fut_syms = {
                    str(r.get("symbol") or "")
                    for r in rank_top(score_futures(universe, cfg), prelim_n)
                }
                # Listings with live book — MTF on the joined market (spot vs futures).
                for lr in rank_top(score_listings(universe, cfg), prelim_n):
                    sym = str(lr.get("symbol") or "")
                    if not sym:
                        continue
                    if str(lr.get("market") or "spot").lower() == "futures":
                        fut_syms.add(sym)
                    else:
                        spot_syms.add(sym)
                # CEX memes share the spot candle book — include in ensure targets.
                spot_syms |= _cex_meme_syms(prelim_n)
                part = ensure_universe_short_windows(
                    http, universe, spot_symbols=spot_syms, fut_symbols=fut_syms
                )
                for section in ("spot", "futures"):
                    dst = ensure_acc.setdefault(section, {})
                    for k, v in (part.get(section) or {}).items():
                        dst[k] = int(dst.get(k) or 0) + int(v or 0)
                ensure_acc["spot_targets"] = int(part.get("spot_targets") or 0)
                ensure_acc["fut_targets"] = int(part.get("fut_targets") or 0)
                ensure_acc["rounds"] = rnd + 1
                # Early-exit only when *final* top-N books are filled (prelim can lie).
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
                meme_top = _cex_meme_syms(top_n)
                if (
                    _ensure_bucket_ok(_ensure_fill_ratio(universe, spot_top, "spot"))
                    and _ensure_bucket_ok(_ensure_fill_ratio(universe, fut_top, "futures"))
                    and _ensure_bucket_ok(_ensure_fill_ratio(universe, list_spot, "spot"))
                    and _ensure_bucket_ok(_ensure_fill_ratio(universe, list_fut, "futures"))
                    and _ensure_bucket_ok(_ensure_fill_ratio(universe, meme_top, "spot"))
                    and bool(spot_top or fut_top or list_spot or list_fut or meme_top)
                ):
                    break
            # Final honesty: incomplete top-N fill is not "ensure ok".
            # Empty target set for a bucket = N/A; ALL buckets empty = fail-closed.
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
            meme_top = _cex_meme_syms(top_n)
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
            safe_ens = redact_exc(exc, max_len=128)
            logger.warning("short-window ensure soft-fail: %s", safe_ens)
            universe["short_window_ensure"] = {"error": safe_ens}
            universe["mtf_ensure_ok"] = False
            errs = list(universe.get("errors") or [])
            errs.append(f"mtf_ensure_failed:{safe_ens}")
            universe["errors"] = errs[:32]
        t_score = int(time.time() * 1000)
        try:
            sectors, backend = score_sectors(universe, cfg, top_n)
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
            "score_backend": backend,
            "score_ms": score_ms,
            "native": native_status(),
            "universe_size": universe.get("universe_size"),
            "http_stats": universe.get("http_stats") or {},
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
    finally:
        http.close()


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
