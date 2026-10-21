from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from radar.auth import (
    api_key_gate_ok,
    api_key_required,
    require_api_key_env,
    verify_api_key,
)
from radar.auth_stats import AUTH_STATS
from radar.config import ROOT, load_config
from radar.ingest.circuit import get_breaker
from radar.ingest.sector_poll import effective_snapshot_refresh_ms
from radar.metrics import build_metrics_payload, to_prometheus
from radar.middleware import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_HEADER_COUNT,
    DEFAULT_MAX_URL_BYTES,
    REQUEST_ID_HEADER,
    MaxBodySizeMiddleware,
    MaxHeadersMiddleware,
    MaxUrlLengthMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    sanitize_access_path,
    sanitize_request_id,
)
from radar.request_ctx import get_request_id
from radar.native_bridge import native_status
from radar.persist import sha256_text
from radar.pipeline import empty_snapshot, run_scan
from radar.rate_limit import RateLimiter
from radar.scan_cache import SnapshotCache
from radar.secrets_redact import configure_secure_logging, redact_exc, redact_text
from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import (
    KuCoinManualTrader,
    TradeRequest,
    extract_order_id,
    resolve_base_asset,
    sanitize_exchange_surface,
    sanitize_trade_request,
)
from radar.telegram import TelegramBot
from radar.telegram.alerts import find_row_public
from radar.trade.events import read_trade_events, record_trade_event
from radar.trade.oid_ledger import ClientOidLedger
from radar.trade.stats import TRADE_STATS
from radar.trade.symbol_meta import SymbolMetaCache

configure_secure_logging()
logger = logging.getLogger("radar.api")

_cache = SnapshotCache()
DASHBOARD = ROOT / "dashboard"


def _build_trader() -> KuCoinManualTrader:
    cfg = load_config().get("auto_trade") or {}
    ttl = int(cfg.get("client_oid_ttl_sec") or 604_800)
    ledger = ClientOidLedger(
        ROOT / "data" / "client_oid_ledger.json",
        ttl_sec=ttl,
        max_entries=int(cfg.get("client_oid_max_entries") or 10_000),
    )
    sell_clamp = bool(cfg.get("sell_clamp_to_balance", True))
    meta_ttl = int(cfg.get("symbol_meta_ttl_sec") or 3600)
    meta = SymbolMetaCache(ttl_sec=meta_ttl)
    enforce_meta = bool(cfg.get("enforce_symbol_meta", True))
    return KuCoinManualTrader(
        ledger,
        sell_clamp=sell_clamp,
        symbol_meta=meta,
        enforce_symbol_meta=enforce_meta,
    )


_trader = _build_trader()
_tg: Optional[TelegramBot] = None
_tg_start_error: Optional[str] = None


def _api_cfg() -> Dict[str, Any]:
    return (load_config().get("api") or {})


def _trade_limiter() -> RateLimiter:
    # Rebuild from config each call is wasteful — use module singleton refreshed on first use
    return _TRADE_LIMITER


_TRADE_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("trade_rate_limit_per_min") or 30)),
    window_ms=60_000,
)
_FORCE_SCAN_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("force_scan_rate_limit_per_min") or 6)),
    window_ms=60_000,
)
_SNAPSHOT_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("snapshot_rate_limit_per_min") or 120)),
    window_ms=60_000,
)
_OPS_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("ops_rate_limit_per_min") or 60)),
    window_ms=60_000,
)
_METRICS_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("metrics_rate_limit_per_min") or 120)),
    window_ms=60_000,
)
_PROBE_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("probe_rate_limit_per_min") or 300)),
    window_ms=60_000,
)
_AUTH_FAIL_LIMITER = RateLimiter(
    max_per_window=int((_api_cfg().get("auth_fail_rate_limit_per_min") or 30)),
    window_ms=60_000,
)


def _refresh_ms() -> int:
    cfg = load_config()
    if cfg.get("snapshot_refresh_ms") is not None:
        base = int(cfg["snapshot_refresh_ms"])
    else:
        base = 45_000
    # Wake outer cache early enough for the fastest ingest group (memes/cex/…).
    return effective_snapshot_refresh_ms(cfg, base)


def _scan(*, force: bool = False) -> Dict[str, Any]:
    cfg = load_config()
    try:
        return run_scan(cfg, force=force)
    except Exception as exc:  # noqa: BLE001
        safe = redact_exc(exc, max_len=256)
        logger.error("scan failed: %s", safe)
        return empty_snapshot(safe, cfg=cfg)


def _get_snapshot(force: bool = False) -> Dict[str, Any]:
    return _cache.get(
        force=force,
        refresh_ms=_refresh_ms(),
        scan_fn=lambda: _scan(force=force),
    )


def _trust_proxy() -> bool:
    """RADAR_TRUST_PROXY=1 — honor X-Forwarded-For for rate-limit keys only."""
    flag = (os.environ.get("RADAR_TRUST_PROXY") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _client_key(request: Request) -> str:
    """Rate-limit identity: peer socket IP by default (ignore spoofable XFF)."""
    if _trust_proxy():
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        # Bound header abuse before split/normalize
        if len(xff) > 512:
            xff = xff[:512]
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return RateLimiter.normalize_key(first)
    if request.client and request.client.host:
        return RateLimiter.normalize_key(request.client.host)
    return "unknown"


def _check_trade_rate(request: Request) -> None:
    allowed, retry_after = _trade_limiter().check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="trade rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def _check_force_scan_rate(request: Request) -> None:
    allowed, retry_after = _FORCE_SCAN_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="force scan rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def _check_snapshot_rate(request: Request) -> None:
    """Soft cap on /api/snapshot reads (cached path still costs JSON/CPU)."""
    allowed, retry_after = _SNAPSHOT_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="snapshot rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def _check_auth_fail_rate(request: Request) -> None:
    """Soft refuse API-key brute force (per client key)."""
    allowed, retry_after = _AUTH_FAIL_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="auth fail rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )



def _client_is_loopback(request: Request) -> bool:
    host = ""
    if request.client is not None:
        host = str(request.client.host or "")
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _check_snapshot_auth(request: Request) -> None:
    """Optional API key for /api/snapshot|/api/sector (ops exposure gate)."""
    api = _api_cfg()
    always = bool(api.get("snapshot_require_api_key"))
    non_loop = bool(api.get("snapshot_require_api_key_non_loopback", True))
    if always:
        _check_api_key(request)
        return
    if non_loop and not _client_is_loopback(request) and api_key_required():
        _check_api_key(request)


def _check_api_key(request: Request) -> None:
    """When RADAR_API_KEY is set, require matching X-Radar-Api-Key.

    RADAR_REQUIRE_API_KEY=1 refuses trade mutations if the key is unset
    (fail-closed for docker/prod — no open trade surface).
    """
    if not api_key_gate_ok():
        AUTH_STATS.inc("auth_fail")
        raise HTTPException(
            503,
            detail="RADAR_REQUIRE_API_KEY set but RADAR_API_KEY missing",
        )
    if not api_key_required():
        return
    provided = request.headers.get("X-Radar-Api-Key")
    if not verify_api_key(provided):
        AUTH_STATS.inc("auth_fail")
        _check_auth_fail_rate(request)
        raise HTTPException(401, detail="missing or invalid X-Radar-Api-Key")
    AUTH_STATS.inc("auth_ok")


def _ops_problems(*, for_ready: bool) -> list[str]:
    """Shared honesty checks for /api/ready and /api/health."""
    snap = _cache.peek()
    auto = _auto.status()
    native = native_status()
    problems: list[str] = []
    if for_ready and snap is None:
        problems.append("no_snapshot_yet")
    elif snap is not None and not snap.get("ok", True):
        problems.append("last_scan_failed")
    if not auto.get("persistence_ok", True):
        problems.append("auto_positions_persist_degraded")
    integrity = auto.get("positions_integrity")
    if integrity in {"mismatch", "corrupt", "too_large"}:
        problems.append(f"positions_integrity_{integrity}")
    prefer = bool((load_config().get("native") or {}).get("prefer", True))
    if prefer and not native.get("available"):
        problems.append("native_prefer_but_unavailable")
    # OID ledger — live trade duplicate protection
    trader_st = _trader.status() if _trader else {}
    oid = trader_st.get("oid_ledger") or {}
    if oid and oid.get("persist_ok") is False:
        problems.append("oid_ledger_persist_degraded")
    if trader_st.get("base_url_ok") is False:
        problems.append("kucoin_base_url_refused")
    if not api_key_gate_ok():
        problems.append("api_key_required_but_unset")
    meta = (trader_st.get("symbol_meta") or {}) if trader_st else {}
    if meta.get("base_url_ok") is False:
        problems.append("symbol_meta_base_url_refused")
    # Telegram watch book (only when bot is configured / running)
    if _tg_start_error:
        problems.append("telegram_start_failed")
    if _tg is not None:
        tg = _tg.status()
        watches = tg.get("watches") or {}
        if (
            (tg.get("running") or tg.get("env_ready"))
            and watches.get("persist_ok") is False
        ):
            problems.append("telegram_watches_persist_degraded")
    if not for_ready:
        circuit = get_breaker(load_config()).status()
        if circuit.get("open_remaining_ms"):
            problems.append("venue_circuit_open")
    return problems


_auto = AutoTradeEngine(_trader, lambda: _get_snapshot(force=False))


def _tg_trade_place(
    *,
    symbol: str,
    side: str,
    confirm: bool,
    quote_usdt_micros: Optional[int] = None,
    base_size_i: Optional[int] = None,
) -> Dict[str, Any]:
    """Spot-only place for Telegram — never futures."""
    result = _trader.place(
        TradeRequest(
            symbol=symbol,
            side=side,
            confirm=bool(confirm),
            quote_usdt_micros=quote_usdt_micros,
            base_size_i=base_size_i,
        )
    )
    out: Dict[str, Any] = {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "message": result.message,
        "request": sanitize_trade_request(result.request),
        "exchange": sanitize_exchange_surface(result.exchange),
    }
    oid = extract_order_id(result.exchange)
    if oid:
        out["order_id"] = oid
    if result.ok and not result.dry_run and oid and side.lower() == "buy":
        out["fill"] = _manual_fill_reconcile(symbol, oid)
    trade_ev = {"source": "telegram", "side": side, "symbol": symbol, **out}
    if out.get("fill"):
        trade_ev["base_size_i"] = out["fill"].get("base_size_i")
        trade_ev["size_basis"] = out["fill"].get("size_basis")
    if str(side).lower() == "buy" and not trade_ev.get("entry_price_i"):
        try:
            snap = _cache.peek() or {}
            row = find_row_public(snap, symbol)
            if row and row.get("price_i"):
                trade_ev["entry_price_i"] = int(row["price_i"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "entry_price_i enrich failed symbol=%s: %s",
                symbol,
                redact_exc(exc, max_len=256),
            )
    record_trade_event(trade_ev)
    if _tg is not None:
        _tg.notify_trade(trade_ev)
        _tg_apply_position_hooks(trade_ev)
    return out



def _telegram_public_status() -> Dict[str, Any]:
    if _tg is None:
        out: Dict[str, Any] = {"running": False}
    else:
        out = dict(_tg.status())
    if _tg_start_error:
        out["start_error"] = _tg_start_error
        out["degraded"] = True
    return out

def _build_telegram() -> TelegramBot:
    return TelegramBot(
        lambda: _get_snapshot(force=False),
        auto_status_fn=lambda: _auto.status(),
        trade_place_fn=_tg_trade_place,
        mark_price_fn=_trader.get_spot_mark_price_i,
    )


class ManualOrderBody(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=32)
    side: str = Field(..., description="buy or sell")
    confirm: bool = False
    quote_usdt_micros: Optional[int] = Field(
        None, gt=0, description="USDT micros (1 USDT = 1_000_000) for market buy"
    )
    base_size_i: Optional[int] = Field(
        None, gt=0, description="base size * 1e8 for market sell"
    )
    reconcile: bool = Field(
        True,
        description="after live order, poll KuCoin dealSize → integer fill",
    )
    client_oid: Optional[str] = Field(
        None,
        min_length=8,
        max_length=64,
        description="idempotency key; duplicate live submit soft-refused",
    )

    @field_validator("symbol")
    @classmethod
    def _symbol_ok(cls, v: str) -> str:
        from radar.trade.symbol_validate import normalize_trade_symbol

        return normalize_trade_symbol(v)

    @field_validator("side")
    @classmethod
    def _side_ok(cls, v: str) -> str:
        s = (v or "").lower().strip()
        if s not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        return s

    @field_validator("client_oid")
    @classmethod
    def _oid_ok(cls, v: Optional[str]) -> Optional[str]:
        from radar.trade.symbol_validate import validate_client_oid

        return validate_client_oid(v)


class AutoToggleBody(BaseModel):
    enabled: bool


class ReconcileBody(BaseModel):
    symbol: Optional[str] = Field(
        None,
        min_length=3,
        max_length=32,
        description="one symbol; omit to refresh all unreconciled bags",
    )

    @field_validator("symbol")
    @classmethod
    def _symbol_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        from radar.trade.symbol_validate import normalize_trade_symbol

        return normalize_trade_symbol(v)


def _tg_apply_position_hooks(event: Dict[str, Any]) -> None:
    """Watch on live buy / unwatch on live sell — spot only."""
    if _tg is None:
        return
    if not event.get("ok") or event.get("dry_run"):
        return
    sym = event.get("symbol")
    if not sym:
        return
    side = str(event.get("side") or "").lower()
    if side == "buy":
        _tg.watch_auto_spot(
            str(sym),
            entry_price_i=int(event["entry_price_i"])
            if event.get("entry_price_i")
            else None,
        )
    elif side == "sell":
        _tg.unwatch_spot(str(sym))


def _on_auto_action(event: Dict[str, Any]) -> None:
    record_trade_event(event)
    if _tg is None:
        return
    _tg.notify_trade(event)
    _tg_apply_position_hooks(event)


def _redact_fs_paths(obj: Any) -> Any:
    """Strip absolute filesystem paths from public JSON surfaces."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in {
                "path",
                "ledger_path",
                "data_dir",
                "positions_path",
                "watches_path",
                "events_path",
            } and isinstance(v, str):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_fs_paths(v)
        return out
    if isinstance(obj, list):
        return [_redact_fs_paths(x) for x in obj]
    return obj


def _public_trade_surface(*, detail: bool = False) -> Dict[str, Any]:
    """Trade status for JSON surfaces.

    ``detail=False`` (snapshot/sector/health): strip open position book —
    symbols/sizes/order ids stay off unauthenticated responses.
    ``detail=True``: full book for API-key gated ops endpoints.
    """
    manual = _redact_fs_paths(_trader.status())
    auto = _redact_fs_paths(_auto.status())
    if not detail and isinstance(auto, dict) and "positions" in auto:
        auto = dict(auto)
        auto["positions"] = {}
        auto["positions_redacted"] = True
    return {"manual_trade": manual, "auto_trade": auto}


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _tg, _tg_start_error
    # Fail-closed startup when docker/prod requires API key but none is set.
    if not api_key_gate_ok() and "pytest" not in sys.modules:
        raise RuntimeError(
            "RADAR_REQUIRE_API_KEY set but RADAR_API_KEY missing — refuse start"
        )
    if not api_key_gate_ok():
        logger.warning("api key gate degraded (pytest / lab)")
    _auto.start()
    _tg = _build_telegram()
    _auto.set_on_action(_on_auto_action)
    _tg_start_error = None
    try:
        started = _tg.start()
        if started:
            pruned = _tg.prune_watches()
            synced = _tg.sync_auto_positions(_auto.status().get("positions") or {})
            logger.info("telegram prune=%s sync_auto=%s", pruned, synced)
        elif _tg.configured():
            _tg_start_error = "telegram configured but start returned false"
            logger.error("%s", _tg_start_error)
    except Exception as exc:  # noqa: BLE001
        _tg_start_error = redact_exc(exc, max_len=256)
        logger.error(
            "telegram start failed — continuing without bot: %s",
            _tg_start_error,
        )
    if bool(_api_cfg().get("warm_scan_on_startup", True)):

        def _warm() -> None:
            try:
                logger.info("warm scan starting")
                _get_snapshot(force=True)
                logger.info("warm scan done ok=%s", (_cache.peek() or {}).get("ok"))
            except Exception as exc:  # noqa: BLE001
                logger.error("warm scan failed: %s", redact_exc(exc, max_len=256))

        threading.Thread(target=_warm, name="radar-warm-scan", daemon=True).start()
    yield
    logger.info("shutdown: stopping telegram + auto-trade")
    _auto.set_on_action(None)
    if _tg is not None:
        _tg.stop(join_timeout_sec=5.0)
    _auto.stop_loop(join_timeout_sec=8.0)


def _openapi_enabled() -> bool:
    """OpenAPI/Swagger off by default — enable via RADAR_ENABLE_OPENAPI or config."""
    env = (os.environ.get("RADAR_ENABLE_OPENAPI") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    return bool((_api_cfg().get("enable_openapi") is True))


_OPENAPI_ON = _openapi_enabled()
app = FastAPI(
    title="DUP Market Radar",
    version="0.2.62",
    lifespan=_lifespan,
    docs_url="/docs" if _OPENAPI_ON else None,
    redoc_url="/redoc" if _OPENAPI_ON else None,
    openapi_url="/openapi.json" if _OPENAPI_ON else None,
)
# Starlette: last added = outermost.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
_max_body = int(
    (_api_cfg().get("max_request_body_bytes") or DEFAULT_MAX_BODY_BYTES)
)
app.add_middleware(MaxBodySizeMiddleware, max_bytes=_max_body)
_max_url = int((_api_cfg().get("max_request_url_bytes") or DEFAULT_MAX_URL_BYTES))
app.add_middleware(MaxUrlLengthMiddleware, max_bytes=_max_url)
_max_hdr_n = int(
    (_api_cfg().get("max_request_header_count") or DEFAULT_MAX_HEADER_COUNT)
)
_max_hdr_b = int(
    (_api_cfg().get("max_request_header_bytes") or DEFAULT_MAX_HEADER_BYTES)
)
app.add_middleware(
    MaxHeadersMiddleware, max_count=_max_hdr_n, max_bytes=_max_hdr_b
)


def _response_request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None) or get_request_id()
    return sanitize_request_id(rid if isinstance(rid, str) else None)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Ensure 4xx/5xx carry X-Request-Id; scrub string details."""
    rid = _response_request_id(request)
    detail = exc.detail
    if isinstance(detail, str):
        detail = redact_text(detail)
    headers = dict(exc.headers) if exc.headers else {}
    headers[REQUEST_ID_HEADER] = rid
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 — never echo raw body/secrets from pydantic errors."""
    rid = _response_request_id(request)
    # Drop input values — keep loc/type/msg only; bound list/string sizes
    safe_errs = []
    for err in list(exc.errors())[:32]:
        loc = err.get("loc") or ()
        safe_errs.append(
            {
                "type": str(err.get("type") or "")[:64],
                "loc": [str(x)[:64] for x in loc][:16],
                "msg": redact_text(str(err.get("msg") or ""))[:256],
            }
        )
    return JSONResponse(
        status_code=422,
        content={"detail": safe_errs},
        headers={REQUEST_ID_HEADER: rid},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Fail-closed 500 — log redacted, never leak stack/secrets to client."""
    rid = _response_request_id(request)
    logger.error(
        "unhandled id=%s path=%s err=%s",
        rid,
        sanitize_access_path(request.url.path),
        redact_exc(exc, max_len=256),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal error", "request_id": rid},
        headers={REQUEST_ID_HEADER: rid},
    )


def _install_cors() -> None:
    origins = list(_api_cfg().get("cors_origins") or [])
    # Fail-closed: never install wildcard. Empty list = no CORS middleware (same-origin only).
    if not origins:
        logger.info("CORS disabled (no api.cors_origins)")
        return
    if "*" in origins:
        raise RuntimeError("api.cors_origins must not contain '*' — refuse to start")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Radar-Api-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    logger.info("CORS allowlist=%s", origins)


def _install_trusted_hosts() -> None:
    hosts = [str(h).strip() for h in (_api_cfg().get("trusted_hosts") or []) if str(h).strip()]
    if not hosts:
        logger.info("TrustedHost disabled (no api.trusted_hosts)")
        return
    if "*" in hosts or any(h == "*.*" for h in hosts):
        raise RuntimeError("api.trusted_hosts must not contain '*' — refuse to start")
    # httpx/Starlette TestClient uses Host: testserver — allow only under pytest.
    if "pytest" in sys.modules and "testserver" not in hosts:
        hosts.append("testserver")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    logger.info("TrustedHost allowlist=%s", hosts)


_install_cors()
_install_trusted_hosts()


def _metrics_dict() -> Dict[str, Any]:
    snap = _cache.peek()
    return build_metrics_payload(
        cache=_cache.status(),
        circuit=get_breaker(load_config()).status(),
        snap=snap,
        auto=_auto.status(),
        native=native_status(),
        trade_limiter=_trade_limiter().status(),
        trade_stats=TRADE_STATS.snapshot(),
        telegram=_telegram_public_status(),
        auth_stats=AUTH_STATS.snapshot(),
    )


def _check_ops_rate(request: Request) -> None:
    allowed, retry_after = _OPS_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="ops rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def _check_metrics_rate(request: Request) -> None:
    allowed, retry_after = _METRICS_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="metrics rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def _check_probe_rate(request: Request) -> None:
    """Soft cap on /api/live|/api/ready|/api/health (k8s-friendly default)."""
    allowed, retry_after = _PROBE_LIMITER.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            429,
            detail="probe rate limit — soft refuse, retry later",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


@app.get("/api/ops/worldtime")
def ops_worldtime(request: Request, lang: str = "en") -> Dict[str, Any]:
    """World clock — fixed UTC offsets (ops UI / Telegram)."""
    _check_ops_rate(request)
    from radar.i18n import normalize_lang
    from radar.ops.world_time import world_clock

    return world_clock(lang=normalize_lang(lang))


@app.get("/api/ops/fx")
def ops_fx(
    request: Request,
    amount: str = "1",
    from_ccy: str = "USD",
    to_ccy: str = "EUR",
) -> Dict[str, Any]:
    """Fiat FX via ECB frankfurter — integer micros."""
    _check_ops_rate(request)
    from radar.ops.fx import FxError, convert, parse_amount_to_micros

    # Bound query abuse before Decimal parse
    if len(amount or "") > 24 or len(from_ccy or "") > 8 or len(to_ccy or "") > 8:
        raise HTTPException(400, detail="fx query too long")
    try:
        micros = parse_amount_to_micros(amount)
        return convert(micros, from_ccy, to_ccy)
    except FxError as exc:
        raise HTTPException(400, detail=redact_exc(exc, max_len=256)) from None


@app.get("/api/ops/bubbles")
def ops_bubbles(request: Request, force: bool = False) -> Dict[str, Any]:
    """Top-100 crypto bubbles — live price + ~6m (CoinGecko 200d) change."""
    _check_ops_rate(request)
    if force:
        _check_force_scan_rate(request)
    from radar.ops.bubbles import get_bubbles

    cfg = (load_config().get("bubbles") or {})
    if cfg.get("enabled") is False:
        raise HTTPException(503, detail="bubbles disabled in config")
    return get_bubbles(
        top_n=int(cfg.get("top_n") or 100),
        cache_ttl_ms=int(cfg.get("cache_ttl_ms") or 30_000),
        force=bool(force),
    )


@app.get("/api/live")
def live(request: Request) -> Dict[str, Any]:
    """Liveness — process is up (k8s livenessProbe). Always 200 if reachable."""
    _check_probe_rate(request)
    return {"ok": True, "live": True, "product": "DUP Market Radar"}


@app.get("/api/ready")
def ready(request: Request) -> Dict[str, Any]:
    """Readiness — safe to take traffic (k8s readinessProbe)."""
    _check_probe_rate(request)
    problems = _ops_problems(for_ready=True)
    auto = _auto.status()
    body = {
        "ok": len(problems) == 0,
        "ready": len(problems) == 0,
        "problems": problems,
        "product": "DUP Market Radar",
        "cache": _cache.status(),
        "positions_integrity": auto.get("positions_integrity"),
    }
    if problems:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/api/health")
def health(request: Request) -> Dict[str, Any]:
    """Honest health — not always green (ops dashboard; not a k8s probe)."""
    _check_probe_rate(request)
    snap = _cache.peek()
    auto = _auto.status()
    problems = _ops_problems(for_ready=False)
    ok = len(problems) == 0
    surface = _public_trade_surface()
    return {
        "ok": ok,
        "problems": problems,
        "product": "DUP Market Radar",
        "schema_version": None if snap is None else snap.get("schema_version"),
        "native": native_status(),
        "manual_trade": surface["manual_trade"],
        "auto_trade": surface["auto_trade"],
        "cache": _cache.status(),
        "circuit": get_breaker(load_config()).status(),
        "trade_rate_limit": _trade_limiter().status(),
        "api_key_required": api_key_required(),
        "api_key_require_env": require_api_key_env(),
        "api_key_gate_ok": api_key_gate_ok(),
        "auth": AUTH_STATS.snapshot(),
        "positions_integrity": auto.get("positions_integrity"),
        "telegram": _redact_fs_paths(
            _telegram_public_status()
        ),
        "last_scan_ok": None if snap is None else bool(snap.get("ok")),
        "score_backend": None if snap is None else snap.get("score_backend"),
        "http_stats": None if snap is None else snap.get("http_stats"),
        "dedupe": None if snap is None else snap.get("dedupe"),
    }


@app.get("/api/metrics")
def metrics_json(request: Request) -> Dict[str, Any]:
    _check_metrics_rate(request)
    return _metrics_dict()


@app.get("/api/metrics/prometheus")
def metrics_prometheus(request: Request) -> Response:
    _check_metrics_rate(request)
    body = to_prometheus(_metrics_dict())
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/trade/status")
def trade_status(request: Request) -> Dict[str, Any]:
    """Full trade/ops status — API key when configured (includes position book)."""
    _check_api_key(request)
    surface = _public_trade_surface(detail=True)
    return {
        "ok": True,
        "manual": surface["manual_trade"],
        "auto": surface["auto_trade"],
        "rate_limit": _trade_limiter().status(),
        "telegram": _redact_fs_paths(
            _telegram_public_status()
        ),
    }


@app.get("/api/telegram/status")
def telegram_status(request: Request) -> Dict[str, Any]:
    _check_api_key(request)
    if _tg is None:
        return {"ok": True, "running": False, "configured": False}
    return {"ok": True, **_redact_fs_paths(_telegram_public_status())}


@app.get("/api/trade/events")
def trade_events(request: Request, limit: int = 50) -> Dict[str, Any]:
    """Last N trade audit events (ops). Newest last — API key when configured."""
    _check_api_key(request)
    _check_trade_rate(request)
    lim = max(1, min(200, int(limit)))
    rows = read_trade_events(limit=lim)
    return {"ok": True, "count": len(rows), "events": rows}


@app.get("/api/trade/auto")
def auto_get(request: Request) -> Dict[str, Any]:
    """Auto-trade status with full position book — API key when configured."""
    _check_api_key(request)
    return {"ok": True, **_redact_fs_paths(_auto.status())}


@app.post("/api/trade/auto")
def auto_set(request: Request, body: AutoToggleBody) -> Dict[str, Any]:
    _check_api_key(request)
    _check_trade_rate(request)
    try:
        status = _auto.set_enabled(bool(body.enabled))
    except RuntimeError as exc:
        raise HTTPException(400, detail=redact_exc(exc, max_len=256)) from None
    return {"ok": True, **status}


@app.post("/api/trade/auto/cycle")
def auto_cycle_now(request: Request) -> Dict[str, Any]:
    """Run one auto cycle immediately (only if enabled)."""
    _check_api_key(request)
    _check_trade_rate(request)
    st = _auto.status()
    if not st.get("enabled"):
        raise HTTPException(400, detail="auto-trade is OFF — enable first")
    return {"ok": True, "cycle": _auto.run_once()}


@app.post("/api/trade/reconcile")
def trade_reconcile(request: Request, body: ReconcileBody) -> Dict[str, Any]:
    """Re-poll KuCoin fills for unreconciled auto positions (integer size_basis)."""
    _check_api_key(request)
    _check_trade_rate(request)
    result = _auto.reconcile_positions(symbol=body.symbol)
    return {"ok": True, **result}


def _manual_fill_reconcile(symbol: str, order_id: str) -> Dict[str, Any]:
    """Poll KuCoin order fill using auto_trade reconcile knobs."""
    cfg = load_config().get("auto_trade") or {}
    attempts = int(cfg.get("fill_reconcile_attempts") or 8)
    delay_ms = int(cfg.get("fill_reconcile_delay_ms") or 250)
    fill = _trader.reconcile_fill(
        order_id,
        max_attempts=attempts,
        delay_ms=delay_ms,
        base_asset=resolve_base_asset(symbol),
    )
    return fill.as_dict()


@app.post("/api/trade/order")
def trade_order(request: Request, body: ManualOrderBody) -> Dict[str, Any]:
    """Manual KuCoin spot market order. Requires confirm=true to send to exchange."""
    _check_api_key(request)
    _check_trade_rate(request)
    # Server-side policy: never place futures / USDTM from this surface.
    sym_u = str(body.symbol or "").upper().replace("-", "").replace("_", "")
    if sym_u.endswith("USDTM") or "USDTM" in sym_u:
        raise HTTPException(400, detail="futures symbols refused — spot only")
    try:
        snap = _cache.peek() or {}
        row = find_row_public(snap, body.symbol)
        if row and str(row.get("market") or "").lower() == "futures":
            raise HTTPException(400, detail="futures market refused — spot only")
        sec = str(row.get("sector") or row.get("_sector") or "") if row else ""
        if sec.startswith("futures"):
            raise HTTPException(400, detail="futures sector refused — spot only")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Fail-closed: never place when the futures policy gate cannot be evaluated.
        logger.error(
            "futures gate check failed — refuse place: %s",
            redact_exc(exc, max_len=128),
        )
        raise HTTPException(
            503, detail="futures policy gate unavailable — refuse place"
        ) from None
    result = _trader.place(
        TradeRequest(
            symbol=body.symbol,
            side=body.side,
            confirm=bool(body.confirm),
            quote_usdt_micros=body.quote_usdt_micros,
            base_size_i=body.base_size_i,
            client_oid=body.client_oid,
        )
    )
    payload: Dict[str, Any] = {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "message": result.message,
        "request": sanitize_trade_request(result.request),
        "exchange": sanitize_exchange_surface(result.exchange),
        "source": "manual",
        "auto_trade_enabled": _auto.status().get("enabled"),
    }
    oid = extract_order_id(result.exchange)
    if oid:
        payload["order_id"] = oid
    if (
        result.ok
        and not result.dry_run
        and bool(body.reconcile)
        and oid
        and bool((load_config().get("auto_trade") or {}).get("fill_reconcile", True))
    ):
        payload["fill"] = _manual_fill_reconcile(body.symbol, oid)
    trade_ev = {
        "source": "manual",
        "side": body.side,
        "symbol": body.symbol,
        "ok": result.ok,
        "dry_run": result.dry_run,
        "message": result.message,
        "order_id": oid,
        "client_oid": body.client_oid,
        "quote_usdt_micros": body.quote_usdt_micros,
        "base_size_i": body.base_size_i,
    }
    if payload.get("fill"):
        trade_ev["base_size_i"] = payload["fill"].get("base_size_i")
        trade_ev["size_basis"] = payload["fill"].get("size_basis")
    if str(body.side).lower() == "buy" and not trade_ev.get("entry_price_i"):
        try:
            snap = _cache.peek() or {}
            row = find_row_public(snap, body.symbol)
            if row and row.get("price_i"):
                trade_ev["entry_price_i"] = int(row["price_i"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "entry_price_i enrich failed symbol=%s: %s",
                body.symbol,
                redact_exc(exc, max_len=256),
            )
    record_trade_event(trade_ev)
    if _tg is not None and (result.ok or not result.dry_run):
        _tg.notify_trade(trade_ev)
        _tg_apply_position_hooks(trade_ev)
    if result.ok:
        return payload
    raise HTTPException(400, detail=payload)


def _snapshot_etag(body: Dict[str, Any]) -> str:
    """Stable weak ETag from score backend + generated_at_ms + universe + integrity."""
    at = body.get("auto_trade") or {}
    raw = "|".join(
        [
            str(body.get("generated_at_ms") or body.get("generated_at") or ""),
            str(body.get("score_backend") or ""),
            str(body.get("universe_size") or ""),
            str(body.get("ok")),
            str(at.get("positions_sha256") or ""),
            str((body.get("cache") or {}).get("stats") or {}),
        ]
    )
    return f'W/"{sha256_text(raw)[:16]}"'


@app.get("/api/snapshot")
def snapshot(request: Request, force: bool = False) -> Response:
    _check_snapshot_rate(request)
    _check_snapshot_auth(request)
    if force:
        _check_force_scan_rate(request)
    snap = dict(_get_snapshot(force=force))
    surface = _public_trade_surface()
    snap["manual_trade"] = surface["manual_trade"]
    snap["auto_trade"] = surface["auto_trade"]
    snap["telegram"] = _redact_fs_paths(_telegram_public_status())
    snap["cache"] = _cache.status()
    etag = _snapshot_etag(snap)
    snap["etag"] = etag
    inm = (request.headers.get("if-none-match") or request.headers.get("If-None-Match") or "")
    # Bound header compare cost / log noise
    if len(inm) > 128:
        inm = ""
    if inm and inm.strip() == etag and not force:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(content=snap, headers={"ETag": etag})


@app.get("/api/sector/{sector_id}")
def sector(request: Request, sector_id: str, force: bool = False) -> Dict[str, Any]:
    sid = (sector_id or "").strip().lower()
    if len(sid) < 2 or len(sid) > 32 or not sid.replace("_", "").isalnum():
        raise HTTPException(400, detail="invalid sector_id")
    _check_snapshot_rate(request)
    _check_snapshot_auth(request)
    if force:
        _check_force_scan_rate(request)
    snap = _get_snapshot(force=force)
    sectors = snap.get("sectors") or {}
    if sid not in sectors:
        raise HTTPException(404, detail="unknown sector")
    meta_key = sid.replace("futures_long", "futures").replace(
        "futures_short", "futures"
    )
    surface = _public_trade_surface()
    return {
        "ok": snap.get("ok"),
        "sector": sid,
        "meta": (snap.get("sector_meta") or {}).get(meta_key),
        "rows": sectors[sid],
        "generated_at": snap.get("generated_at"),
        "errors": snap.get("errors"),
        "disclaimer": snap.get("disclaimer"),
        "manual_trade": surface["manual_trade"],
        "auto_trade": surface["auto_trade"],
    }


@app.get("/")
def index() -> FileResponse:
    path = DASHBOARD / "index.html"
    if not path.exists():
        raise HTTPException(500, detail="dashboard missing")
    return FileResponse(path)


if DASHBOARD.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD)), name="static")
