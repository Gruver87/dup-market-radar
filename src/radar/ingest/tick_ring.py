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

logger = logging.getLogger("radar.ingest.tick_ring")

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"

_LOCK = threading.RLock()
_FEED: Optional["NativeWsFeed"] = None

_VENUE_BINS = {
    "binance": "radar-ingest-binance",
    "bybit": "radar-ingest-bybit",
    "kucoin": "radar-ingest-kucoin",
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


class NativeWsFeed:
    def __init__(
        self,
        *,
        venue: str = "binance",
        symbols: Optional[List[str]] = None,
        ring_size: int = 512,
        stale_ms: int = 15_000,
    ) -> None:
        self.venue = (venue or "binance").strip().lower()
        self.symbols = [s.strip().upper() for s in (symbols or ["BTCUSDT"]) if s.strip()]
        self.ring_size = max(32, min(8192, int(ring_size)))
        self.stale_ms = max(1_000, min(300_000, int(stale_ms)))
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=self.ring_size)
        self._last: Dict[str, Dict[str, Any]] = {}
        self._proc: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started_at_ms = 0
        self._ticks_total = 0
        self._last_error: Optional[str] = None
        self._running = False

    def start(self) -> bool:
        with _LOCK:
            if self._running:
                return True
            cmd = resolve_ingest_cmd(self.venue, self.symbols)
            if not cmd:
                self._last_error = "ingest_binary_unavailable"
                logger.warning("native_ws start refused: %s", self._last_error)
                return False
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                self._last_error = f"spawn_failed:{exc}"
                logger.warning("native_ws spawn failed: %s", self._last_error)
                return False
            self._stop.clear()
            self._started_at_ms = int(time.time() * 1000)
            self._running = True
            self._thread = threading.Thread(
                target=self._reader, name=f"radar-native-ws-{self.venue}", daemon=True
            )
            self._thread.start()
            logger.info(
                "native_ws started venue=%s symbols=%s pid=%s",
                self.venue,
                ",".join(self.symbols),
                getattr(self._proc, "pid", None),
            )
            return True

    def stop(self, join_timeout_sec: float = 3.0) -> None:
        with _LOCK:
            self._stop.set()
            proc = self._proc
            self._proc = None
            self._running = False
        if proc is not None:
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
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=join_timeout_sec)

    def _reader(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
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
                with _LOCK:
                    self._ring.append(row)
                    self._last[sym] = row
                    self._ticks_total += 1
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"reader:{exc}"[:200]
            logger.warning("native_ws reader stop: %s", self._last_error)
        finally:
            with _LOCK:
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
                self._last_error = f"exit_{rc}:{err}"[:240]

    def push_for_test(self, tick: Dict[str, Any]) -> None:
        """Unit-test helper — inject one tick without spawning WS."""
        sym = str(tick.get("symbol") or "").upper()
        if not sym:
            return
        row = dict(tick)
        row.setdefault("recv_ms", int(time.time() * 1000))
        with _LOCK:
            self._ring.append(row)
            self._last[sym] = row
            self._ticks_total += 1

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
            running = bool(self._running and self._proc is not None)
            n = len(self._ring)
            total = self._ticks_total
            err = self._last_error
            started = self._started_at_ms
        stale = freshest_age is None or freshest_age > self.stale_ms
        ok = running and total > 0 and not stale
        return {
            "enabled": True,
            "ok": ok,
            "running": running,
            "venue": self.venue,
            "symbols": list(self.symbols),
            "ring_n": n,
            "ticks_total": total,
            "stale_ms": self.stale_ms,
            "freshest_age_ms": freshest_age,
            "started_at_ms": started or None,
            "last_by_symbol": last_by,
            "replaces_http_poll": False,
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
    return {
        "enabled": bool(nws.get("enabled")),
        "venue": venue,
        "symbols": list(nws.get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        "ring_size": int(nws.get("ring_size") or 512),
        "stale_ms": int(nws.get("stale_ms") or 15_000),
        # Attach ws_* on sector rows when age ok (never mutates price_i)
        "annotate_rows": bool(nws.get("annotate_rows", True)),
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
            symbols=list(opts["symbols"]),  # type: ignore[arg-type]
            ring_size=int(opts["ring_size"]),  # type: ignore[arg-type]
            stale_ms=int(opts["stale_ms"]),  # type: ignore[arg-type]
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
        }
    return feed.status()
