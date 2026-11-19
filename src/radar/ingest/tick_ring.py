"""Additive native WS tick ring — does NOT replace HTTP poll snapshot prices.

Spawns ``radar-ingest-{binance|bybit}`` (release binary preferred) and keeps a
bounded last-N + last-by-symbol view for honesty overlays / future merge.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from radar.ingest.bar_agg import BarAggregator, merge_interval_metrics, metrics_from_bars

logger = logging.getLogger("radar.ingest.tick_ring")

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"

_LOCK = threading.RLock()
_FEED: Optional["NativeWsFeed"] = None

_VENUE_BINS = {
    "binance": "radar-ingest-binance",
    "bybit": "radar-ingest-bybit",
    "kucoin": "radar-ingest-kucoin",
    "mexc": "radar-ingest-mexc",
}


def _bin_name(venue: str) -> str:
    v = (venue or "binance").strip().lower()
    return _VENUE_BINS.get(v, "radar-ingest-binance")


def _release_binary_candidates(name: str) -> List[Path]:
    """Search common release dirs (local target + CARGO_TARGET_DIR)."""
    suffix = ".exe" if os.name == "nt" else ""
    fname = name + suffix
    out: List[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    _add(NATIVE / "target" / "release" / fname)
    env_td = (os.environ.get("CARGO_TARGET_DIR") or "").strip()
    if env_td:
        _add(Path(env_td) / "release" / fname)
    # Cursor/sandbox sometimes parks cargo target under Local\Temp\...\cargo-target
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        base = Path(temp)
        try:
            for child in base.glob("cursor-sandbox-cache/*/cargo-target/release"):
                _add(child / fname)
        except OSError:
            pass
    return out


def resolve_ingest_cmd(venue: str, symbols: List[str]) -> Optional[List[str]]:
    """Prefer built release binary; else cargo run. None if neither available."""
    name = _bin_name(venue)
    sym = ",".join(symbols) if symbols else "BTCUSDT"
    args_tail = ["--seconds", "0", "--symbols", sym]
    for exe in _release_binary_candidates(name):
        if exe.is_file():
            return [str(exe), *args_tail]
    cargo = shutil.which("cargo")
    if not cargo or not (NATIVE / "Cargo.toml").is_file():
        return None
    return [
        cargo,
        "run",
        "-p",
        "radar_ingest",
        "--release",
        "--bin",
        name,
        "--manifest-path",
        str(NATIVE / "Cargo.toml"),
        "--",
        *args_tail,
    ]


def _normalize_venues(
    venue: str, venues: Optional[List[str]] = None
) -> List[str]:
    """Dedupe venue list; fall back to single ``venue``; cap 3."""
    raw: List[str] = []
    if venues:
        for v in venues:
            s = str(v or "").strip().lower()
            if s in _VENUE_BINS and s not in raw:
                raw.append(s)
    if not raw:
        primary = str(venue or "binance").strip().lower()
        if primary not in _VENUE_BINS:
            primary = "binance"
        raw = [primary]
    return raw[:3]


def _normalize_symbols(raw: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for s in raw or []:
        u = str(s or "").strip().upper()
        if u and u not in out:
            out.append(u)
    return out[:32]


def _symbols_by_venue(
    venues: List[str],
    default_symbols: Optional[List[str]],
    venue_symbols: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    """Per-venue subscribe lists; missing override → default ``symbols``."""
    base = _normalize_symbols(default_symbols) or ["BTCUSDT"]
    ov = venue_symbols if isinstance(venue_symbols, dict) else {}
    out: Dict[str, List[str]] = {}
    for v in venues:
        raw = ov.get(v)
        if isinstance(raw, list) and raw:
            got = _normalize_symbols(raw)
            out[v] = got or list(base)
        else:
            out[v] = list(base)
    return out


class NativeWsFeed:
    def __init__(
        self,
        *,
        venue: str = "binance",
        venues: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        venue_symbols: Optional[Dict[str, Any]] = None,
        ring_size: int = 512,
        stale_ms: int = 15_000,
        bar_interval_sec: int = 60,
        bar_intervals: Optional[List[int]] = None,
    ) -> None:
        self.venues = _normalize_venues(venue, venues)
        self.venue = self.venues[0]
        self.symbols_by_venue = _symbols_by_venue(
            self.venues, symbols, venue_symbols
        )
        # Union — bars/last cover every symbol any venue may emit
        union: List[str] = []
        for ven in self.venues:
            for s in self.symbols_by_venue.get(ven) or []:
                if s not in union:
                    union.append(s)
        self.symbols = union or _normalize_symbols(symbols) or ["BTCUSDT"]
        self.ring_size = max(32, min(8192, int(ring_size)))
        self.stale_ms = max(1_000, min(300_000, int(stale_ms)))
        # Cap 14400 = 4h WS bar; primary fold still often 60.
        self.bar_interval_sec = max(5, min(14400, int(bar_interval_sec)))
        raw_iv = (
            list(bar_intervals)
            if bar_intervals
            else [self.bar_interval_sec, 300, 900, 3600, 14400]
        )
        cleaned: List[int] = []
        for x in raw_iv:
            try:
                v = max(5, min(14400, int(x)))
            except (TypeError, ValueError):
                continue
            if v not in cleaned:
                cleaned.append(v)
        if self.bar_interval_sec not in cleaned:
            cleaned.insert(0, self.bar_interval_sec)
        self.bar_intervals = cleaned[:5] or [60, 300, 900, 3600, 14400]
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=self.ring_size)
        self._last: Dict[str, Dict[str, Any]] = {}
        self._aggs: Dict[str, Dict[int, BarAggregator]] = {
            s: {iv: BarAggregator(iv) for iv in self.bar_intervals} for s in self.symbols
        }
        self._prev_closed: Dict[str, Dict[int, Any]] = {s: {} for s in self.symbols}
        self._closed_n = 0
        self._procs: List[subprocess.Popen[str]] = []
        self._threads: List[threading.Thread] = []
        # Back-compat aliases for tests / older callers
        self._proc: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started_at_ms = 0
        self._ticks_total = 0
        self._last_error: Optional[str] = None
        self._running = False
        self._venue_errors: Dict[str, str] = {}

    def _ingest_tick_locked(self, row: Dict[str, Any]) -> None:
        """Caller holds _LOCK."""
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            return
        self._ring.append(row)
        self._last[sym] = row
        self._ticks_total += 1
        by_iv = self._aggs.get(sym)
        if by_iv is None:
            by_iv = {iv: BarAggregator(iv) for iv in self.bar_intervals}
            self._aggs[sym] = by_iv
            self._prev_closed[sym] = {}
        for iv, agg in by_iv.items():
            closed = agg.push(row)
            if closed is not None:
                self._prev_closed.setdefault(sym, {})[iv] = closed
                self._closed_n += 1

    def _any_proc_alive(self) -> bool:
        return any(p.poll() is None for p in self._procs)

    def start(self) -> bool:
        with _LOCK:
            if self._running:
                return True
            self._stop.clear()
            self._procs = []
            self._threads = []
            self._venue_errors = {}
            started = 0
            for ven in self.venues:
                ven_syms = list(
                    self.symbols_by_venue.get(ven) or self.symbols
                )
                if not ven_syms:
                    self._venue_errors[ven] = "no_symbols"
                    continue
                cmd = resolve_ingest_cmd(ven, ven_syms)
                if not cmd:
                    self._venue_errors[ven] = "ingest_binary_unavailable"
                    logger.warning(
                        "native_ws start refused venue=%s: ingest_binary_unavailable",
                        ven,
                    )
                    continue
                try:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(ROOT),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                except OSError as exc:
                    self._venue_errors[ven] = f"spawn_failed:{exc}"
                    logger.warning(
                        "native_ws spawn failed venue=%s: %s", ven, exc
                    )
                    continue
                self._procs.append(proc)
                th = threading.Thread(
                    target=self._reader,
                    args=(proc, ven),
                    name=f"radar-native-ws-{ven}",
                    daemon=True,
                )
                self._threads.append(th)
                th.start()
                started += 1
                logger.info(
                    "native_ws started venue=%s symbols=%s pid=%s",
                    ven,
                    ",".join(ven_syms),
                    getattr(proc, "pid", None),
                )
            if started <= 0:
                self._last_error = (
                    self._venue_errors.get(self.venue)
                    or next(iter(self._venue_errors.values()), None)
                    or "ingest_binary_unavailable"
                )
                self._proc = None
                self._thread = None
                return False
            self._started_at_ms = int(time.time() * 1000)
            self._running = True
            self._proc = self._procs[0]
            self._thread = self._threads[0]
            if self._venue_errors:
                self._last_error = ";".join(
                    f"{k}:{v}" for k, v in self._venue_errors.items()
                )[:240]
            return True

    def stop(self, join_timeout_sec: float = 3.0) -> None:
        with _LOCK:
            self._stop.set()
            procs = list(self._procs)
            self._procs = []
            self._proc = None
            self._running = False
            threads = list(self._threads)
            self._threads = []
            self._thread = None
        for proc in procs:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=max(0.5, float(join_timeout_sec)))
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except OSError:
                    pass
        for th in threads:
            if th is not None and th.is_alive():
                th.join(timeout=join_timeout_sec)

    def _reader(self, proc: subprocess.Popen[str], venue: str) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = (line or "").strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("price_i") is None:
                    continue
                sym = str(row.get("symbol") or "").upper()
                if not sym:
                    continue
                now = int(time.time() * 1000)
                row = dict(row)
                row["recv_ms"] = now
                row.setdefault("venue", venue)
                with _LOCK:
                    self._ingest_tick_locked(row)
        except Exception as exc:  # noqa: BLE001
            err = f"reader:{exc}"[:200]
            self._last_error = err
            self._venue_errors[venue] = err
            logger.warning("native_ws reader stop venue=%s: %s", venue, err)
        finally:
            with _LOCK:
                if not self._any_proc_alive():
                    self._running = False
            rc = None
            try:
                if proc.poll() is None:
                    proc.terminate()
                rc = proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            if rc not in (0, None) and not self._stop.is_set():
                err = ""
                try:
                    if proc.stderr:
                        err = (proc.stderr.read() or "")[-300:]
                except Exception:  # noqa: BLE001
                    pass
                msg = f"exit_{rc}:{err}"[:240]
                self._last_error = msg
                self._venue_errors[venue] = msg

    def push_for_test(self, tick: Dict[str, Any]) -> None:
        """Unit-test helper — inject one tick without spawning WS."""
        sym = str(tick.get("symbol") or "").upper()
        if not sym:
            return
        row = dict(tick)
        row.setdefault("recv_ms", int(time.time() * 1000))
        with _LOCK:
            self._ingest_tick_locked(row)

    def bar_metrics_by_symbol(self) -> Dict[str, Dict[str, Any]]:
        with _LOCK:
            out: Dict[str, Dict[str, Any]] = {}
            for sym, by_iv in self._aggs.items():
                prev_map = self._prev_closed.get(sym) or {}
                parts = [
                    metrics_from_bars(
                        current=agg.current, prev_closed=prev_map.get(iv)
                    )
                    for iv, agg in by_iv.items()
                ]
                out[sym] = merge_interval_metrics(parts)
            return out

    def status(self) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        with _LOCK:
            last_by: Dict[str, Any] = {}
            freshest_age: Optional[int] = None
            for sym, row in self._last.items():
                ts = int(row.get("ts_ms") or row.get("recv_ms") or 0)
                age = max(0, now - ts) if ts else None
                if age is not None and (freshest_age is None or age < freshest_age):
                    freshest_age = age
                last_by[sym] = {
                    "price_i": row.get("price_i"),
                    "qty_i": row.get("qty_i"),
                    "ts_ms": row.get("ts_ms"),
                    "side": row.get("side"),
                    "age_ms": age,
                    "venue": row.get("venue") or self.venue,
                }
            bars: Dict[str, Dict[str, Any]] = {}
            for sym, by_iv in self._aggs.items():
                prev_map = self._prev_closed.get(sym) or {}
                parts = [
                    metrics_from_bars(
                        current=agg.current, prev_closed=prev_map.get(iv)
                    )
                    for iv, agg in by_iv.items()
                ]
                bars[sym] = merge_interval_metrics(parts)
            alive = self._any_proc_alive()
            running = bool(self._running and alive)
            n = len(self._ring)
            total = self._ticks_total
            closed_n = self._closed_n
            err = self._last_error
            started = self._started_at_ms
            bar_iv = self.bar_interval_sec
            bar_ivs = list(self.bar_intervals)
            venues = list(self.venues)
            venue_errs = dict(self._venue_errors)
            n_procs = len(self._procs)
            by_ven = {k: list(v) for k, v in self.symbols_by_venue.items()}
        stale = freshest_age is None or freshest_age > self.stale_ms
        ok = running and total > 0 and not stale
        return {
            "enabled": True,
            "ok": ok,
            "running": running,
            "venue": self.venue,
            "venues": venues,
            "venue_procs_n": n_procs,
            "venue_errors": venue_errs or None,
            "symbols": list(self.symbols),
            "symbols_by_venue": by_ven,
            "ring_n": n,
            "ticks_total": total,
            "stale_ms": self.stale_ms,
            "freshest_age_ms": freshest_age,
            "started_at_ms": started or None,
            "last_by_symbol": last_by,
            "bar_interval_sec": bar_iv,
            "bar_intervals": bar_ivs,
            "bars_closed_n": closed_n,
            "bar_metrics_by_symbol": bars,
            "replaces_http_poll": False,  # annotate-only; apply_ws_to_price_i is separate
            "error": err,
        }


def configure_from_cfg(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    ingest = (cfg or {}).get("ingest") or {}
    nws = ingest.get("native_ws") if isinstance(ingest, dict) else None
    if not isinstance(nws, dict):
        return {
            "enabled": False,
            "ok": False,
            "replaces_http_poll": False,
            "annotate_rows": False,
        }
    venue = str(nws.get("venue") or "binance").lower()
    if venue not in _VENUE_BINS:
        venue = "binance"
    raw_venues = nws.get("venues")
    venues_list: Optional[List[str]] = None
    if isinstance(raw_venues, list):
        venues_list = [str(x) for x in raw_venues]
    venues = _normalize_venues(venue, venues_list)
    default_syms = _normalize_symbols(
        list(nws.get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ) or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    raw_vs = nws.get("venue_symbols")
    venue_symbols = raw_vs if isinstance(raw_vs, dict) else None
    by_ven = _symbols_by_venue(venues, default_syms, venue_symbols)
    union: List[str] = []
    for ven in venues:
        for s in by_ven.get(ven) or []:
            if s not in union:
                union.append(s)
    return {
        "enabled": bool(nws.get("enabled")),
        "venue": venues[0],
        "venues": venues,
        "symbols": default_syms,
        "symbols_union": union or list(default_syms),
        "symbols_by_venue": by_ven,
        "venue_symbols": venue_symbols,
        "ring_size": int(nws.get("ring_size") or 512),
        "stale_ms": int(nws.get("stale_ms") or 15_000),
        # Attach ws_* on sector rows when age ok (never mutates price_i)
        "annotate_rows": bool(nws.get("annotate_rows", True)),
        # Explicit: overwrite universe price_i before score (keeps price_i_http)
        "apply_ws_to_price_i": bool(nws.get("apply_ws_to_price_i", False)),
        "bar_interval_sec": int(nws.get("bar_interval_sec") or 60),
        "bar_intervals": list(
            nws.get("bar_intervals") or [60, 300, 900, 3600, 14400]
        ),
        "stamp_bar_metrics": bool(nws.get("stamp_bar_metrics", True)),
        "skip_http_kline_when_ws": bool(nws.get("skip_http_kline_when_ws", True)),
        # After WS 15m promote/skip: HTTP higher-TF only when WS 1h/4h not dense
        "htf_backfill_when_ws": bool(nws.get("htf_backfill_when_ws", True)),
    }


def start_from_config(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    """Start global feed if ingest.native_ws.enabled. Idempotent."""
    global _FEED
    opts = configure_from_cfg(cfg)
    if not opts.get("enabled"):
        return {"enabled": False, "ok": False, "replaces_http_poll": False}
    with _LOCK:
        if _FEED is not None and _FEED._running:  # noqa: SLF001
            return _FEED.status()
        if _FEED is not None:
            _FEED.stop()
        feed = NativeWsFeed(
            venue=str(opts["venue"]),
            venues=list(opts.get("venues") or [opts["venue"]]),  # type: ignore[arg-type]
            symbols=list(opts["symbols"]),  # type: ignore[arg-type]
            venue_symbols=opts.get("venue_symbols")
            if isinstance(opts.get("venue_symbols"), dict)
            else None,
            ring_size=int(opts["ring_size"]),  # type: ignore[arg-type]
            stale_ms=int(opts["stale_ms"]),  # type: ignore[arg-type]
            bar_interval_sec=int(opts.get("bar_interval_sec") or 60),
            bar_intervals=list(
                opts.get("bar_intervals") or [60, 300, 900, 3600, 14400]
            ),  # type: ignore[arg-type]
        )
        ok = feed.start()
        _FEED = feed if ok else feed
        return feed.status()


def stop_feed(join_timeout_sec: float = 3.0) -> None:
    global _FEED
    with _LOCK:
        feed = _FEED
        _FEED = None
    if feed is not None:
        feed.stop(join_timeout_sec=join_timeout_sec)


def overlay_status(cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Snapshot field — always present; honesty: never invents prices."""
    opts = configure_from_cfg(cfg)
    if not opts.get("enabled"):
        return {
            "enabled": False,
            "ok": False,
            "replaces_http_poll": False,
            "note": "set ingest.native_ws.enabled=true to attach Rust WS ticks",
        }
    with _LOCK:
        feed = _FEED
    if feed is None:
        return {
            "enabled": True,
            "ok": False,
            "running": False,
            "replaces_http_poll": False,
            "error": "feed_not_started",
            "venue": opts.get("venue"),
            "venues": opts.get("venues"),
        }
    return feed.status()


def bar_metrics_snapshot() -> Dict[str, Dict[str, Any]]:
    """Current WS bar metrics by symbol (empty if feed down)."""
    with _LOCK:
        feed = _FEED
    if feed is None:
        return {}
    return feed.bar_metrics_by_symbol()
