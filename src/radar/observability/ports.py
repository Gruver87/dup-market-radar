"""MetricsExporterPort — immutable scrape snapshot + Prometheus render."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class MetricsSnapshot:
    """Immutable scrape DTO — built on the HTTP worker thread, not the scan loop."""

    product: str = "DUP Market Radar"
    deployment_mode: str = "lab"
    schema_version: Optional[int] = None
    score_backend: str = ""
    last_scan_ok: Optional[bool] = None
    universe_size: Optional[int] = None
    elapsed_ms: Optional[int] = None
    ingest_ms: Optional[int] = None
    ensure_ms: Optional[int] = None
    score_ms: Optional[int] = None
    kline_gets: Optional[int] = None
    ensure_rounds: Optional[int] = None
    venue_ms: Mapping[str, Any] = field(default_factory=dict)
    ensure_stage_ms: Mapping[str, Any] = field(default_factory=dict)
    native_available: bool = False
    cache: Mapping[str, Any] = field(default_factory=dict)
    circuit: Mapping[str, Any] = field(default_factory=dict)
    http: Mapping[str, Any] = field(default_factory=dict)
    dedupe: Mapping[str, Any] = field(default_factory=dict)
    auto_trade: Mapping[str, Any] = field(default_factory=dict)
    trade_rate_limit: Mapping[str, Any] = field(default_factory=dict)
    trade: Mapping[str, Any] = field(default_factory=dict)
    auth: Mapping[str, Any] = field(default_factory=dict)
    telegram: Mapping[str, Any] = field(default_factory=dict)
    recovery: Mapping[str, Any] = field(default_factory=dict)
    secrets_backend: str = "env"


@runtime_checkable
class MetricsExporterPort(Protocol):
    def render(self, snapshot: MetricsSnapshot) -> str:
        ...


def snapshot_from_payload(payload: Dict[str, Any], *, deployment_mode: str = "lab") -> MetricsSnapshot:
    """Build frozen snapshot from existing JSON metrics payload (+ optional recovery)."""
    return MetricsSnapshot(
        product=str(payload.get("product") or "DUP Market Radar"),
        deployment_mode=deployment_mode,
        schema_version=payload.get("schema_version"),
        score_backend=str(payload.get("score_backend") or ""),
        last_scan_ok=payload.get("last_scan_ok"),
        universe_size=payload.get("universe_size"),
        elapsed_ms=payload.get("elapsed_ms"),
        ingest_ms=payload.get("ingest_ms"),
        ensure_ms=payload.get("ensure_ms"),
        score_ms=payload.get("score_ms"),
        kline_gets=payload.get("kline_gets"),
        ensure_rounds=payload.get("ensure_rounds"),
        venue_ms=dict(payload.get("venue_ms") or {}),
        ensure_stage_ms=dict(payload.get("ensure_stage_ms") or {}),
        native_available=bool(payload.get("native_available")),
        cache=dict(payload.get("cache") or {}),
        circuit=dict(payload.get("circuit") or {}),
        http=dict(payload.get("http") or {}),
        dedupe=dict(payload.get("dedupe") or {}),
        auto_trade=dict(payload.get("auto_trade") or {}),
        trade_rate_limit=dict(payload.get("trade_rate_limit") or {}),
        trade=dict(payload.get("trade") or {}),
        auth=dict(payload.get("auth") or {}),
        telegram=dict(payload.get("telegram") or {}),
        recovery=dict(payload.get("recovery") or {}),
        secrets_backend=str(payload.get("secrets_backend") or "env"),
    )
