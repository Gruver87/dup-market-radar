from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from radar.config import venue_priority
from radar.http_client import AllowlistHttp
from radar.ingest.binance import fetch_binance
from radar.ingest.bybit import fetch_bybit
from radar.ingest.circuit import get_breaker
from radar.ingest.coingecko import fetch_stable_prices
from radar.ingest.dedupe import dedupe_by_symbol, dedupe_stats
from radar.ingest.dexscreener import fetch_meme_pairs
from radar.ingest.kucoin import fetch_kucoin
from radar.ingest.mexc import fetch_mexc
from radar.ingest.normalize import enrich_list
from radar.ingest.sector_poll import poll_state
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.universe")


def build_universe(
    http: AllowlistHttp,
    cfg: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Parallel ingest on the shared AllowlistHttp (thread-safe TLS + cache).

    Honors sectors.*.poll_sec via fetch groups (cex / memes / stables).
    Stale groups are served from SectorPollState cache.
    Caller owns http lifecycle (do not close here — ensure reuses the same client).
    """
    venues = cfg.get("venues") or ["kucoin", "binance", "bybit", "mexc"]
    ingest_cfg = cfg.get("ingest") or {}
    breaker = get_breaker(cfg)
    skipped_circuit: List[str] = []
    state = poll_state()
    due = state.due_groups(cfg, force=force)
    cached = state.cached_slices()
    skipped_poll: List[str] = []

    # Local error list + shared http.errors (workers may append onto http).
    errors: List[str] = []
    err_baseline = len(http.errors)
    spot: List[Dict[str, Any]] = []
    futures: List[Dict[str, Any]] = []
    listings: List[Dict[str, Any]] = []
    whales: List[Dict[str, Any]] = []
    memes: List[Dict[str, Any]] = list(cached.get("memes") or [])
    stables: List[Dict[str, Any]] = list(cached.get("stables") or [])

    fetchers: Dict[str, Callable[[AllowlistHttp], Dict[str, Any]]] = {
        "kucoin": fetch_kucoin,
        "binance": fetch_binance,
        "bybit": fetch_bybit,
        "mexc": fetch_mexc,
    }

    memes_cfg = (cfg.get("sectors") or {}).get("memes") or {}
    stables_cfg = (cfg.get("sectors") or {}).get("stables") or {}

    def run_venue(name: str) -> tuple[str, Any]:
        if not breaker.allow(name):
            skipped_circuit.append(name)
            raise RuntimeError(f"circuit_open:{name}")
        try:
            pack = fetchers[name](http)
            breaker.record_ok(name)
            return name, pack
        except Exception as exc:
            breaker.record_fail(name, redact_exc(exc, max_len=120))
            raise

    def run_memes() -> tuple[str, Any]:
        name = "dexscreener"
        if not breaker.allow(name):
            skipped_circuit.append(name)
            raise RuntimeError(f"circuit_open:{name}")
        try:
            pack = fetch_meme_pairs(http, list(memes_cfg.get("chains") or []))
            breaker.record_ok(name)
            return name, pack
        except Exception as exc:
            breaker.record_fail(name, redact_exc(exc, max_len=120))
            raise

    def run_stables() -> tuple[str, Any]:
        name = "coingecko"
        if not breaker.allow(name):
            skipped_circuit.append(name)
            raise RuntimeError(f"circuit_open:{name}")
        try:
            pack = fetch_stable_prices(http, list(stables_cfg.get("symbols") or []))
            breaker.record_ok(name)
            return name, pack
        except Exception as exc:
            breaker.record_fail(name, redact_exc(exc, max_len=120))
            raise

    work: List[Callable[[], tuple[str, Any]]] = []
    fetch_cex = "cex" in due
    fetch_memes = "memes" in due
    fetch_stables = "stables" in due

    if fetch_cex:
        for name in venues:
            if name in fetchers:
                if not breaker.allow(name):
                    skipped_circuit.append(name)
                    errors.append(f"circuit_open_skip:{name}")
                    continue
                work.append(lambda n=name: run_venue(n))
    else:
        skipped_poll.append("cex")
        spot = list(cached.get("spot") or [])
        futures = list(cached.get("futures") or [])
        listings = list(cached.get("listings") or [])
        whales = list(cached.get("whales") or [])

    if fetch_memes:
        if breaker.allow("dexscreener"):
            work.append(run_memes)
        else:
            skipped_circuit.append("dexscreener")
            errors.append("circuit_open_skip:dexscreener")
    else:
        skipped_poll.append("memes")

    if fetch_stables:
        if breaker.allow("coingecko"):
            work.append(run_stables)
        else:
            skipped_circuit.append("coingecko")
            errors.append("circuit_open_skip:coingecko")
    else:
        skipped_poll.append("stables")

    max_workers = int(ingest_cfg.get("parallel_workers") or 6)
    cex_fresh = False
    memes_fresh = False
    stables_fresh = False
    if work:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(fn) for fn in work]
            for fut in as_completed(futs):
                try:
                    name, pack = fut.result()
                except Exception as exc:  # noqa: BLE001
                    raw = str(exc)
                    safe = redact_exc(exc, max_len=256)
                    if raw.startswith("circuit_open"):
                        msg = raw[:64]
                    else:
                        msg = f"ingest worker failed: {safe}"
                    errors.append(msg)
                    logger.warning(msg)
                    continue
                if name == "dexscreener":
                    memes = enrich_list(pack if isinstance(pack, list) else [])
                    memes_fresh = True
                elif name == "coingecko":
                    stables = enrich_list(pack if isinstance(pack, list) else [])
                    stables_fresh = True
                elif isinstance(pack, dict):
                    spot.extend(enrich_list(pack.get("spot") or []))
                    futures.extend(enrich_list(pack.get("futures") or []))
                    listings.extend(enrich_list(pack.get("listings") or []))
                    whales.extend(enrich_list(pack.get("whales") or []))
                    cex_fresh = True

    pri = venue_priority(cfg)
    if fetch_cex and cex_fresh:
        spot_before, fut_before = len(spot), len(futures)
        spot = dedupe_by_symbol(spot, pri)
        futures = dedupe_by_symbol(futures, pri)
        listings = dedupe_by_symbol(listings, pri, key_fields=("symbol",))
        state.store_cex(
            spot=spot, futures=futures, listings=listings, whales=whales
        )
        dedupe_info = {
            "spot": dedupe_stats(spot_before, len(spot)),
            "futures": dedupe_stats(fut_before, len(futures)),
            "venue_priority": pri,
        }
    else:
        if fetch_cex and not cex_fresh and (
            cached.get("spot") or cached.get("futures")
        ):
            spot = list(cached.get("spot") or [])
            futures = list(cached.get("futures") or [])
            listings = list(cached.get("listings") or [])
            whales = list(cached.get("whales") or [])
            errors.append("cex_fetch_failed_using_cache")
        dedupe_info = {
            "spot": dedupe_stats(len(spot), len(spot)),
            "futures": dedupe_stats(len(futures), len(futures)),
            "venue_priority": pri,
        }

    if memes_fresh:
        state.store_memes(memes)
    if stables_fresh:
        state.store_stables(stables)

    # Merge http-ring errors from this build with local scheduling messages.
    for msg in http.errors[err_baseline:]:
        if msg not in errors:
            errors.append(msg)
    n_err = len(errors)
    if n_err > 64:
        errors = errors[:64]
        errors.append(f"errors_truncated:{n_err}")
    http_stats = {k: int(v or 0) for k, v in (http.stats or {}).items()}
    return {
        "generated_at_ms": int(time.time() * 1000),
        "generated_at": int(time.time()),
        "spot": spot,
        "futures": futures,
        "listings": listings,
        "whales": whales,
        "memes": memes,
        "stables": stables,
        "universe_size": len(spot) + len(futures) + len(memes),
        "errors": errors,
        "ingest_parallel": True,
        "http_shared": True,
        "http_stats": http_stats,
        "circuit": breaker.status(),
        "circuit_skipped": skipped_circuit[:32],
        "poll_skipped": skipped_poll[:8],
        "poll": state.status(cfg),
        "due_groups": sorted(due),
        "dedupe": dedupe_info,
    }
