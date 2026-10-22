"""Ops metrics — JSON + Prometheus text exposition (MetricsExporterPort)."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from radar.observability.ports import MetricsExporterPort, MetricsSnapshot, snapshot_from_payload

_PROM_LABEL_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _prom_label_value(raw: Any, *, max_len: int = 64) -> str:
    """Sanitize Prometheus label values — refuse quote/control injection."""
    s = _PROM_LABEL_RE.sub("_", str(raw or ""))
    s = s.strip("._-")[: max(1, int(max_len))]
    return s or "unknown"


def build_metrics_payload(
    *,
    cache: Optional[Dict[str, Any]] = None,
    circuit: Optional[Dict[str, Any]] = None,
    snap: Optional[Dict[str, Any]] = None,
    auto: Optional[Dict[str, Any]] = None,
    native: Optional[Dict[str, Any]] = None,
    trade_limiter: Optional[Dict[str, Any]] = None,
    trade_stats: Optional[Dict[str, Any]] = None,
    telegram: Optional[Dict[str, Any]] = None,
    auth_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    http = (snap or {}).get("http_stats") or {}
    dedupe = (snap or {}).get("dedupe") or {}
    tg = telegram or {}
    watches = tg.get("watches") or {}
    tg_circuit = tg.get("circuit") or {}
    tg_stats = tg.get("stats") or {}
    auth = auth_stats or {}
    return {
        "ok": True,
        "product": "DUP Market Radar",
        "schema_version": (snap or {}).get("schema_version"),
        "cache": cache or {},
        "circuit": circuit or {},
        "http": http,
        "dedupe": dedupe,
        "score_backend": (snap or {}).get("score_backend"),
        "last_scan_ok": None if snap is None else bool(snap.get("ok")),
        "universe_size": (snap or {}).get("universe_size"),
        "elapsed_ms": (snap or {}).get("elapsed_ms"),
        "ingest_ms": (snap or {}).get("ingest_ms"),
        "ensure_ms": (snap or {}).get("ensure_ms"),
        "score_ms": (snap or {}).get("score_ms"),
        "kline_gets": ((snap or {}).get("http_stats") or {}).get("kline_gets"),
        "ensure_rounds": ((snap or {}).get("short_window_ensure") or {}).get("rounds"),
        "venue_ms": (snap or {}).get("venue_ms") or {},
        "ensure_stage_ms": ((snap or {}).get("short_window_ensure") or {}).get("stage_ms")
        or {},
        "auto_trade": {
            "enabled": bool((auto or {}).get("enabled")),
            "open_positions": (auto or {}).get("open_positions"),
            "persistence_ok": (auto or {}).get("persistence_ok"),
        },
        "native_available": bool((native or {}).get("available")),
        "trade_rate_limit": trade_limiter or {},
        "trade": trade_stats or {},
        "auth": {
            "auth_ok": int(auth.get("auth_ok") or 0),
            "auth_fail": int(auth.get("auth_fail") or 0),
        },
        "telegram": {
            "running": bool(tg.get("running")),
            "watches": watches.get("count"),
            "watches_persist_ok": watches.get("persist_ok"),
            "circuit_open": len(tg_circuit.get("open_remaining_ms") or {}),
            "alerts_sent": int(tg_stats.get("alerts_sent") or 0),
            "alerts_skipped_stale": int(tg_stats.get("alerts_skipped_stale") or 0),
            "alerts_skipped_empty": int(tg_stats.get("alerts_skipped_empty") or 0),
            "alerts_skipped_degraded": int(tg_stats.get("alerts_skipped_degraded") or 0),
            "alerts_mark_miss": int(tg_stats.get("alerts_mark_miss") or 0),
            "watch_add_fail": int(tg_stats.get("watch_add_fail") or 0),
            "cmd_rate_refused": int(tg_stats.get("cmd_rate_refused") or 0),
        },
    }


class PrometheusMetricsExporter:
    """Hand-built Prometheus 0.0.4 text — fail-closed on NaN/Inf."""

    def render(self, snapshot: MetricsSnapshot) -> str:
        payload = {
            "product": snapshot.product,
            "schema_version": snapshot.schema_version,
            "score_backend": snapshot.score_backend,
            "last_scan_ok": snapshot.last_scan_ok,
            "universe_size": snapshot.universe_size,
            "elapsed_ms": snapshot.elapsed_ms,
            "ingest_ms": snapshot.ingest_ms,
            "ensure_ms": snapshot.ensure_ms,
            "score_ms": snapshot.score_ms,
            "kline_gets": snapshot.kline_gets,
            "ensure_rounds": snapshot.ensure_rounds,
            "venue_ms": dict(snapshot.venue_ms),
            "ensure_stage_ms": dict(snapshot.ensure_stage_ms),
            "native_available": snapshot.native_available,
            "cache": dict(snapshot.cache),
            "circuit": dict(snapshot.circuit),
            "http": dict(snapshot.http),
            "dedupe": dict(snapshot.dedupe),
            "auto_trade": dict(snapshot.auto_trade),
            "trade_rate_limit": dict(snapshot.trade_rate_limit),
            "trade": dict(snapshot.trade),
            "auth": dict(snapshot.auth),
            "telegram": dict(snapshot.telegram),
            "recovery": dict(snapshot.recovery),
            "deployment_mode": snapshot.deployment_mode,
            "secrets_backend": snapshot.secrets_backend,
        }
        return _render_prometheus(payload)


_EXPORTER: MetricsExporterPort = PrometheusMetricsExporter()


def get_metrics_exporter() -> MetricsExporterPort:
    return _EXPORTER


def to_prometheus(payload: Dict[str, Any], *, deployment_mode: str = "lab") -> str:
    snap = snapshot_from_payload(payload, deployment_mode=deployment_mode)
    return get_metrics_exporter().render(snap)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _render_prometheus(payload: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# HELP radar_up 1 if process serving metrics",
        "# TYPE radar_up gauge",
        "radar_up 1",
    ]

    def gauge(name: str, value: Any, labels: str = "") -> None:
        if value is None:
            return
        v = _safe_float(value)
        if v is None:
            return
        if isinstance(value, bool):
            v = 1.0 if value else 0.0
        lab = f"{{{labels}}}" if labels else ""
        lines.append(f"{name}{lab} {v}")

    cache = payload.get("cache") or {}
    stats = cache.get("stats") or {}
    gauge("radar_cache_hits_total", stats.get("hits"))
    gauge("radar_cache_misses_total", stats.get("misses"))
    gauge("radar_cache_scans_total", stats.get("scans"))
    gauge("radar_cache_scan_errors_total", stats.get("scan_errors"))
    gauge("radar_cache_single_flight_waits_total", stats.get("single_flight_waits"))
    gauge("radar_cache_inflight", 1 if cache.get("inflight") else 0)
    gauge("radar_cache_age_ms", cache.get("age_ms"))

    http = payload.get("http") or {}
    gauge("radar_http_gets_total", http.get("gets"))
    gauge("radar_http_retries_total", http.get("retries"))
    gauge("radar_http_rate_limited_total", http.get("rate_limited"))
    gauge("radar_http_cache_hits_total", http.get("cache_hits"))
    gauge("radar_http_response_too_large_total", http.get("response_too_large"))

    circuit = payload.get("circuit") or {}
    open_rem = circuit.get("open_remaining_ms") or {}
    if not isinstance(open_rem, dict):
        open_rem = {}
    gauge("radar_circuit_open_venues", len(open_rem))
    # Bound label cardinality noise
    for venue, rem in list(open_rem.items())[:32]:
        safe = _prom_label_value(venue)
        gauge("radar_circuit_open_remaining_ms", rem, f'venue="{safe}"')

    gauge("radar_last_scan_ok", 1 if payload.get("last_scan_ok") else 0)
    gauge("radar_universe_size", payload.get("universe_size"))
    gauge("radar_scan_elapsed_ms", payload.get("elapsed_ms"))
    gauge("radar_ingest_ms", payload.get("ingest_ms"))
    gauge("radar_ensure_ms", payload.get("ensure_ms"))
    gauge("radar_score_ms", payload.get("score_ms"))
    gauge("radar_kline_gets", payload.get("kline_gets"))
    gauge("radar_ensure_rounds", payload.get("ensure_rounds"))
    venue_ms = payload.get("venue_ms") or {}
    if isinstance(venue_ms, dict):
        for vname, ms in list(venue_ms.items())[:16]:
            gauge(
                "radar_venue_ms",
                ms,
                f'venue="{_prom_label_value(vname)}"',
            )
    stage_ms = payload.get("ensure_stage_ms") or {}
    if isinstance(stage_ms, dict):
        for sname, ms in list(stage_ms.items())[:16]:
            gauge(
                "radar_ensure_stage_ms",
                ms,
                f'stage="{_prom_label_value(sname)}"',
            )
    gauge("radar_native_available", 1 if payload.get("native_available") else 0)

    auto = payload.get("auto_trade") or {}
    gauge("radar_auto_enabled", 1 if auto.get("enabled") else 0)
    gauge("radar_auto_open_positions", auto.get("open_positions"))
    gauge("radar_auto_persistence_ok", 1 if auto.get("persistence_ok", True) else 0)

    rl = payload.get("trade_rate_limit") or {}
    gauge("radar_trade_rate_allowed_total", rl.get("allowed"))
    gauge("radar_trade_rate_refused_total", rl.get("refused"))

    trade = payload.get("trade") or {}
    gauge("radar_trade_place_ok_total", trade.get("place_ok"))
    gauge("radar_trade_place_fail_total", trade.get("place_fail"))
    gauge("radar_trade_place_preview_total", trade.get("place_preview"))
    gauge("radar_trade_duplicate_oid_total", trade.get("duplicate_oid"))
    gauge("radar_trade_fills_ok_total", trade.get("fills_ok"))
    gauge("radar_trade_fills_fail_total", trade.get("fills_fail"))
    gauge("radar_trade_sell_clamped_total", trade.get("sell_clamped"))

    dedupe = payload.get("dedupe") or {}
    spot = dedupe.get("spot") or {}
    gauge("radar_dedupe_spot_dropped", spot.get("dropped"))

    tg = payload.get("telegram") or {}
    gauge("radar_telegram_running", 1 if tg.get("running") else 0)
    gauge("radar_telegram_watches", tg.get("watches"))
    gauge(
        "radar_telegram_watches_persist_ok",
        1 if tg.get("watches_persist_ok", True) else 0,
    )
    gauge("radar_telegram_circuit_open", tg.get("circuit_open"))
    gauge("radar_telegram_alerts_sent_total", tg.get("alerts_sent"))
    gauge("radar_telegram_alerts_skipped_stale_total", tg.get("alerts_skipped_stale"))
    gauge("radar_telegram_alerts_skipped_empty_total", tg.get("alerts_skipped_empty"))
    gauge("radar_telegram_alerts_skipped_degraded_total", tg.get("alerts_skipped_degraded"))
    gauge("radar_telegram_alerts_mark_miss_total", tg.get("alerts_mark_miss"))
    gauge("radar_telegram_watch_add_fail_total", tg.get("watch_add_fail"))
    gauge("radar_telegram_cmd_rate_refused_total", tg.get("cmd_rate_refused"))

    auth = payload.get("auth") or {}
    gauge("radar_auth_ok_total", auth.get("auth_ok"))
    gauge("radar_auth_fail_total", auth.get("auth_fail"))

    rec = payload.get("recovery") or {}
    gauge("radar_recovery_running", 1 if rec.get("running") else 0)
    gauge("radar_recovery_consecutive_scan_fails", rec.get("consecutive_scan_fails"))
    stats = rec.get("stats") or {}
    gauge("radar_recovery_network_recoveries_total", stats.get("network_recoveries"))
    gauge("radar_recovery_auto_restarts_total", stats.get("auto_restarts"))
    gauge("radar_recovery_telegram_restarts_total", stats.get("telegram_restarts"))
    gauge("radar_recovery_background_scans_total", stats.get("background_scans"))

    mode = _prom_label_value(payload.get("deployment_mode") or "lab")
    backend = _prom_label_value(payload.get("secrets_backend") or "env")
    lines.append(
        f'radar_deployment_info{{mode="{mode}",secrets_backend="{backend}"}} 1'
    )

    lines.append("")
    return "\n".join(lines)
