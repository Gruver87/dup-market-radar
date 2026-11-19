"""Python BarAggregator — mirrors radar_core::BarAggregator (integer money)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence


def bar_open_ms(ts_ms: int, interval_sec: int) -> Optional[int]:
    if interval_sec <= 0 or ts_ms < 0:
        return None
    step = int(interval_sec) * 1000
    return ts_ms - (ts_ms % step)


def change_bps(from_i: int, to_i: int) -> Optional[int]:
    if from_i <= 0:
        return None
    return int((int(to_i) - int(from_i)) * 10_000 // int(from_i))


def _suffix(interval_sec: int) -> str:
    if interval_sec == 60:
        return "1m"
    if interval_sec == 300:
        return "5m"
    if interval_sec == 900:
        return "15m"
    if interval_sec == 3600:
        return "1h"
    if interval_sec == 14400:
        return "4h"
    return f"{int(interval_sec)}s"


@dataclass
class Bar:
    venue: str
    symbol: str
    open_ms: int
    open_i: int
    high_i: int
    low_i: int
    close_i: int
    volume_i: int
    interval_sec: int
    trade_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "open_ms": self.open_ms,
            "open_i": self.open_i,
            "high_i": self.high_i,
            "low_i": self.low_i,
            "close_i": self.close_i,
            "volume_i": self.volume_i,
            "interval_sec": self.interval_sec,
            "trade_count": self.trade_count,
        }


class BarAggregator:
    def __init__(self, interval_sec: int = 60) -> None:
        self.interval_sec = max(1, int(interval_sec))
        self.current: Optional[Bar] = None

    def push(self, tick: Dict[str, Any]) -> Optional[Bar]:
        try:
            ts = int(tick.get("ts_ms") or 0)
            px = int(tick["price_i"])
            qty = int(tick.get("qty_i") or 0)
        except (TypeError, ValueError, KeyError):
            return None
        open_ms = bar_open_ms(ts, self.interval_sec)
        if open_ms is None:
            return None
        sym = str(tick.get("symbol") or "").upper()
        venue = str(tick.get("venue") or "unknown")
        if not sym:
            return None
        if self.current is None:
            self.current = Bar(
                venue=venue,
                symbol=sym,
                open_ms=open_ms,
                open_i=px,
                high_i=px,
                low_i=px,
                close_i=px,
                volume_i=max(0, qty),
                interval_sec=self.interval_sec,
                trade_count=1,
            )
            return None
        bar = self.current
        if bar.open_ms == open_ms and bar.symbol == sym:
            if px > bar.high_i:
                bar.high_i = px
            if px < bar.low_i:
                bar.low_i = px
            bar.close_i = px
            bar.volume_i = bar.volume_i + max(0, qty)
            bar.trade_count += 1
            return None
        closed = bar
        self.current = Bar(
            venue=venue,
            symbol=sym,
            open_ms=open_ms,
            open_i=px,
            high_i=px,
            low_i=px,
            close_i=px,
            volume_i=max(0, qty),
            interval_sec=self.interval_sec,
            trade_count=1,
        )
        return closed

    def flush(self) -> Optional[Bar]:
        b = self.current
        self.current = None
        return b


def metrics_from_bars(
    *,
    current: Optional[Bar],
    prev_closed: Optional[Bar],
) -> Dict[str, Any]:
    """Additive WS bar metrics for one interval — never invent HTTP MTF fields."""
    out: Dict[str, Any] = {}
    if current is None:
        return out
    suf = _suffix(current.interval_sec)
    out[f"ws_bar_{suf}_open_i"] = current.open_i
    out[f"ws_bar_{suf}_close_i"] = current.close_i
    out[f"ws_bar_{suf}_high_i"] = current.high_i
    out[f"ws_bar_{suf}_low_i"] = current.low_i
    out[f"ws_bar_{suf}_trade_count"] = current.trade_count
    out[f"ws_bar_{suf}_open_ms"] = current.open_ms
    out[f"ws_bar_{suf}_interval_sec"] = current.interval_sec
    intra = change_bps(current.open_i, current.close_i)
    if intra is not None:
        out[f"change_bps_ws_{suf}"] = intra
        # Back-compat alias used by spot score / stamp
        if suf == "1m":
            out["change_bps_ws_1m"] = intra
            out["ws_bar_open_i"] = current.open_i
            out["ws_bar_close_i"] = current.close_i
            out["ws_bar_interval_sec"] = current.interval_sec
    if prev_closed is not None:
        vs_prev = change_bps(prev_closed.close_i, current.close_i)
        if vs_prev is not None:
            out[f"change_bps_ws_{suf}_vs_prev"] = vs_prev
            if suf == "1m":
                out["change_bps_ws_vs_prev"] = vs_prev
    return out


def merge_interval_metrics(
    parts: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in parts:
        out.update(p)
    return out
