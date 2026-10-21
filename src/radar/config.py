"""Config load + fail-closed validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"
# Soft refuse absurd YAML (memory DoS).
_CONFIG_MAX_BYTES = 1_048_576  # 1 MiB

KNOWN_VENUES = frozenset({"kucoin", "binance", "bybit", "mexc"})
REQUIRED_SECTORS = frozenset(
    {"spot_alts", "futures", "memes", "stables", "listings", "whales"}
)


class ConfigError(ValueError):
    """Invalid / incomplete config — refuse to start scan."""


def apply_env_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Env overlays for containers (no secrets in YAML)."""
    native = dict(cfg.get("native") or {})
    prefer = os.environ.get("RADAR_NATIVE_PREFER")
    if prefer is not None:
        native["prefer"] = prefer.strip() in {"1", "true", "True", "yes", "YES"}
    fail_closed = os.environ.get("RADAR_NATIVE_FAIL_CLOSED")
    if fail_closed is not None:
        native["fail_closed"] = fail_closed.strip() in {"1", "true", "True", "yes", "YES"}
    if prefer is not None or fail_closed is not None:
        cfg = dict(cfg)
        cfg["native"] = native
    snap_req = os.environ.get("RADAR_SNAPSHOT_REQUIRE_API_KEY")
    if snap_req is not None:
        cfg = dict(cfg)
        api = dict(cfg.get("api") or {})
        api["snapshot_require_api_key"] = snap_req.strip() in {"1", "true", "True", "yes", "YES"}
        cfg["api"] = api
    return cfg


def load_config(path: Path | None = None) -> Dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG
    try:
        size = cfg_path.stat().st_size
    except OSError as exc:
        raise ConfigError(
            f"config unreadable: {cfg_path.name}: {type(exc).__name__}"
        ) from exc
    if size > _CONFIG_MAX_BYTES:
        raise ConfigError(
            f"config too large: {size} > {_CONFIG_MAX_BYTES} bytes ({cfg_path.name})"
        )
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"config must be a mapping: {cfg_path.name}")
    data = apply_env_overrides(data)
    validate_config(data)
    return data


def validate_config(cfg: Dict[str, Any]) -> None:
    errors: List[str] = []

    top_n = cfg.get("top_n")
    if not isinstance(top_n, int) or top_n < 1 or top_n > 100:
        errors.append("top_n must be int 1..100")

    venues = cfg.get("venues")
    if not isinstance(venues, list) or not venues:
        errors.append("venues must be non-empty list")
    elif len(venues) > 8:
        errors.append("venues must have ≤8 entries")
    else:
        bad = [v for v in venues if v not in KNOWN_VENUES]
        if bad:
            errors.append(f"unknown venues: {bad}")
        if "kucoin" not in venues:
            errors.append("venues must include kucoin (primary trading venue)")

    hosts = cfg.get("allowlist_hosts")
    if not isinstance(hosts, list) or not hosts:
        errors.append("allowlist_hosts must be non-empty list")
    elif len(hosts) > 64:
        errors.append("allowlist_hosts must have ≤64 entries")
    elif any(not isinstance(h, str) or not h for h in hosts):
        errors.append("allowlist_hosts entries must be non-empty strings")
    elif any("*" in str(h) or str(h).strip() in {".", ".."} for h in hosts):
        errors.append("allowlist_hosts must not contain wildcards or '.'/'..'")
    elif any(
        not isinstance(h, str) or len(h) > 253 or "/" in h or "://" in h for h in hosts
    ):
        errors.append("allowlist_hosts must be bare hostnames (no URL/path, ≤253)")

    sectors = cfg.get("sectors")
    if not isinstance(sectors, dict):
        errors.append("sectors must be a mapping")
    else:
        missing = REQUIRED_SECTORS - set(sectors.keys())
        if missing:
            errors.append(f"sectors missing keys: {sorted(missing)}")
        for sk, sec in sectors.items():
            if not isinstance(sec, dict):
                errors.append(f"sectors.{sk} must be a mapping")
                continue
            if "poll_sec" in sec:
                ps = sec.get("poll_sec")
                if not isinstance(ps, int) or isinstance(ps, bool) or ps < 5 or ps > 3600:
                    errors.append(f"sectors.{sk}.poll_sec must be int 5..3600")

    native = cfg.get("native")
    if not isinstance(native, dict):
        errors.append("native must be a mapping")
    else:
        if "prefer" in native and not isinstance(native["prefer"], bool):
            errors.append("native.prefer must be bool")
        if "fail_closed" in native and not isinstance(native["fail_closed"], bool):
            errors.append("native.fail_closed must be bool")

    money = cfg.get("money")
    if money is not None:
        if not isinstance(money, dict):
            errors.append("money must be a mapping")
        else:
            for k in ("price_scale", "quote_scale", "base_scale"):
                if k in money and (not isinstance(money[k], int) or money[k] <= 0):
                    errors.append(f"money.{k} must be positive int")

    auto = cfg.get("auto_trade")
    if auto is not None:
        if not isinstance(auto, dict):
            errors.append("auto_trade must be a mapping")
        else:
            for k in (
                "quote_usdt_micros_per_buy",
                "min_score_centi",
                "stop_loss_bps",
                "max_open_positions",
                "cycle_sec",
                "positions_backup_keep",
                "fill_reconcile_attempts",
                "fill_reconcile_delay_ms",
                "audit_log_max_bytes",
                "audit_log_keep",
                "client_oid_ttl_sec",
                "client_oid_max_entries",
                "symbol_meta_ttl_sec",
            ):
                if k in auto and not isinstance(auto[k], int):
                    errors.append(f"auto_trade.{k} must be int")
            if "positions_backup_keep" in auto and (
                auto["positions_backup_keep"] < 0 or auto["positions_backup_keep"] > 50
            ):
                errors.append("auto_trade.positions_backup_keep must be int 0..50")
            if "quote_usdt_micros_per_buy" in auto and auto["quote_usdt_micros_per_buy"] <= 0:
                errors.append("auto_trade.quote_usdt_micros_per_buy must be > 0")
            if "fill_reconcile" in auto and not isinstance(auto["fill_reconcile"], bool):
                errors.append("auto_trade.fill_reconcile must be bool")
            if "sell_clamp_to_balance" in auto and not isinstance(
                auto["sell_clamp_to_balance"], bool
            ):
                errors.append("auto_trade.sell_clamp_to_balance must be bool")
            if "enforce_symbol_meta" in auto and not isinstance(
                auto["enforce_symbol_meta"], bool
            ):
                errors.append("auto_trade.enforce_symbol_meta must be bool")
            if "symbol_meta_ttl_sec" in auto and (
                auto["symbol_meta_ttl_sec"] < 60 or auto["symbol_meta_ttl_sec"] > 86_400
            ):
                errors.append("auto_trade.symbol_meta_ttl_sec must be int 60..86400")
            if "client_oid_ttl_sec" in auto and (
                auto["client_oid_ttl_sec"] < 60 or auto["client_oid_ttl_sec"] > 30 * 86400
            ):
                errors.append("auto_trade.client_oid_ttl_sec must be int 60..2592000")
            if "client_oid_max_entries" in auto and (
                auto["client_oid_max_entries"] < 100 or auto["client_oid_max_entries"] > 100_000
            ):
                errors.append("auto_trade.client_oid_max_entries must be int 100..100000")
            if "fill_reconcile_attempts" in auto and (
                auto["fill_reconcile_attempts"] < 1 or auto["fill_reconcile_attempts"] > 30
            ):
                errors.append("auto_trade.fill_reconcile_attempts must be int 1..30")
            if "fill_reconcile_delay_ms" in auto and (
                auto["fill_reconcile_delay_ms"] < 0 or auto["fill_reconcile_delay_ms"] > 10_000
            ):
                errors.append("auto_trade.fill_reconcile_delay_ms must be int 0..10000")
            if "audit_log_max_bytes" in auto and (
                auto["audit_log_max_bytes"] < 1024 or auto["audit_log_max_bytes"] > 100_000_000
            ):
                errors.append("auto_trade.audit_log_max_bytes must be int 1024..1e8")
            if "audit_log_keep" in auto and (
                auto["audit_log_keep"] < 0 or auto["audit_log_keep"] > 50
            ):
                errors.append("auto_trade.audit_log_keep must be int 0..50")
            allowed = auto.get("allowed_sectors")
            if allowed is not None:
                if not isinstance(allowed, list):
                    errors.append("auto_trade.allowed_sectors must be list")
                elif any(s in {"futures", "futures_long", "futures_short"} for s in allowed):
                    errors.append("auto_trade.allowed_sectors must not include futures")
            for list_key in ("symbol_blacklist", "symbol_whitelist"):
                lst = auto.get(list_key)
                if lst is not None:
                    if not isinstance(lst, list):
                        errors.append(f"auto_trade.{list_key} must be list")
                    elif len(lst) > 500:
                        errors.append(f"auto_trade.{list_key} must have ≤500 entries")
                    elif any(not isinstance(s, str) or not str(s).strip() for s in lst):
                        errors.append(
                            f"auto_trade.{list_key} entries must be non-empty strings"
                        )

    tg = cfg.get("telegram")
    if tg is not None:
        if not isinstance(tg, dict):
            errors.append("telegram must be a mapping")
        else:
            if "enabled" in tg and not isinstance(tg["enabled"], bool):
                errors.append("telegram.enabled must be bool")
            if "notify_trades" in tg and not isinstance(tg["notify_trades"], bool):
                errors.append("telegram.notify_trades must be bool")
            for k in (
                "alert_cycle_sec",
                "alert_cooldown_ms",
                "stop_loss_bps",
                "take_profit_warn_bps",
                "circuit_fail_threshold",
                "circuit_open_ms",
                "watch_ttl_sec",
                "snapshot_max_age_ms",
            ):
                if k in tg and not isinstance(tg[k], int):
                    errors.append(f"telegram.{k} must be int")
            if "alert_cycle_sec" in tg and (
                tg["alert_cycle_sec"] < 15 or tg["alert_cycle_sec"] > 3600
            ):
                errors.append("telegram.alert_cycle_sec must be int 15..3600")
            if "circuit_fail_threshold" in tg and (
                tg["circuit_fail_threshold"] < 1 or tg["circuit_fail_threshold"] > 20
            ):
                errors.append("telegram.circuit_fail_threshold must be int 1..20")
            if "watch_ttl_sec" in tg and (
                tg["watch_ttl_sec"] < 0 or tg["watch_ttl_sec"] > 365 * 86400
            ):
                errors.append("telegram.watch_ttl_sec must be int 0..31536000")
            if "snapshot_max_age_ms" in tg and (
                tg["snapshot_max_age_ms"] < 0 or tg["snapshot_max_age_ms"] > 3_600_000
            ):
                errors.append("telegram.snapshot_max_age_ms must be int 0..3600000")
            crl = tg.get("cmd_rate_limit_per_min")
            if crl is not None and (
                not isinstance(crl, int) or crl < 1 or crl > 120
            ):
                errors.append("telegram.cmd_rate_limit_per_min must be int 1..120")
            mw = tg.get("max_watches")
            if mw is not None and (
                not isinstance(mw, int) or mw < 1 or mw > 50_000
            ):
                errors.append("telegram.max_watches must be int 1..50000")

    # Timeouts: prefer ms ints
    for key in ("http_timeout_ms", "cache_ttl_ms", "snapshot_refresh_ms"):
        if key in cfg and (not isinstance(cfg[key], int) or cfg[key] <= 0):
            errors.append(f"{key} must be positive int")

    api = cfg.get("api")
    if api is not None:
        if not isinstance(api, dict):
            errors.append("api must be a mapping")
        else:
            origins = api.get("cors_origins")
            if origins is not None:
                if not isinstance(origins, list):
                    errors.append("api.cors_origins must be list")
                elif len(origins) > 32:
                    errors.append("api.cors_origins must have ≤32 entries")
                elif any(o == "*" for o in origins):
                    errors.append("api.cors_origins must not contain '*' (fail-closed)")
                elif any(not isinstance(o, str) or not o for o in origins):
                    errors.append("api.cors_origins entries must be non-empty strings")
            hosts = api.get("trusted_hosts")
            if hosts is not None:
                if not isinstance(hosts, list):
                    errors.append("api.trusted_hosts must be list")
                elif len(hosts) > 32:
                    errors.append("api.trusted_hosts must have ≤32 entries")
                elif any(h in {"*", "*.*"} for h in hosts):
                    errors.append("api.trusted_hosts must not contain '*' (fail-closed)")
                elif any(not isinstance(h, str) or not str(h).strip() for h in hosts):
                    errors.append("api.trusted_hosts entries must be non-empty strings")
            trl = api.get("trade_rate_limit_per_min")
            if trl is not None and (not isinstance(trl, int) or trl < 1 or trl > 1000):
                errors.append("api.trade_rate_limit_per_min must be int 1..1000")
            frl = api.get("force_scan_rate_limit_per_min")
            if frl is not None and (not isinstance(frl, int) or frl < 1 or frl > 120):
                errors.append("api.force_scan_rate_limit_per_min must be int 1..120")
            srl = api.get("snapshot_rate_limit_per_min")
            if srl is not None and (
                not isinstance(srl, int) or srl < 1 or srl > 10_000
            ):
                errors.append("api.snapshot_rate_limit_per_min must be int 1..10000")
            orl = api.get("ops_rate_limit_per_min")
            if orl is not None and (
                not isinstance(orl, int) or orl < 1 or orl > 10_000
            ):
                errors.append("api.ops_rate_limit_per_min must be int 1..10000")
            mrl = api.get("metrics_rate_limit_per_min")
            if mrl is not None and (
                not isinstance(mrl, int) or mrl < 1 or mrl > 10_000
            ):
                errors.append("api.metrics_rate_limit_per_min must be int 1..10000")
            prl = api.get("probe_rate_limit_per_min")
            if prl is not None and (
                not isinstance(prl, int) or prl < 1 or prl > 10_000
            ):
                errors.append("api.probe_rate_limit_per_min must be int 1..10000")
            afr = api.get("auth_fail_rate_limit_per_min")
            if afr is not None and (
                not isinstance(afr, int) or afr < 1 or afr > 10_000
            ):
                errors.append("api.auth_fail_rate_limit_per_min must be int 1..10000")
            if "enable_openapi" in api and not isinstance(api["enable_openapi"], bool):
                errors.append("api.enable_openapi must be bool")
            max_q = api.get("max_order_quote_usdt_micros")
            if max_q is not None and (
                not isinstance(max_q, int) or max_q < 1_000_000 or max_q > 100_000_000_000
            ):
                errors.append(
                    "api.max_order_quote_usdt_micros must be int 1e6..1e11 (1..100000 USDT)"
                )
            max_b = api.get("max_order_base_size_i")
            if max_b is not None and (
                not isinstance(max_b, int) or max_b < 1 or max_b > 10**18
            ):
                errors.append("api.max_order_base_size_i must be int 1..1e18")
            if "warm_scan_on_startup" in api and not isinstance(
                api["warm_scan_on_startup"], bool
            ):
                errors.append("api.warm_scan_on_startup must be bool")
            mbody = api.get("max_request_body_bytes")
            if mbody is not None and (
                not isinstance(mbody, int) or mbody < 1024 or mbody > 10_485_760
            ):
                errors.append("api.max_request_body_bytes must be int 1024..10485760")
            murl = api.get("max_request_url_bytes")
            if murl is not None and (
                not isinstance(murl, int) or murl < 256 or murl > 16_384
            ):
                errors.append("api.max_request_url_bytes must be int 256..16384")
            mhc = api.get("max_request_header_count")
            if mhc is not None and (
                not isinstance(mhc, int) or mhc < 16 or mhc > 512
            ):
                errors.append("api.max_request_header_count must be int 16..512")
            mhb = api.get("max_request_header_bytes")
            if mhb is not None and (
                not isinstance(mhb, int) or mhb < 1024 or mhb > 262_144
            ):
                errors.append("api.max_request_header_bytes must be int 1024..262144")
            # Auto buy size must not exceed manual fat-finger cap when both set
            auto_q = (cfg.get("auto_trade") or {}).get("quote_usdt_micros_per_buy")
            if (
                isinstance(max_q, int)
                and isinstance(auto_q, int)
                and auto_q > max_q
            ):
                errors.append(
                    "auto_trade.quote_usdt_micros_per_buy must be <= "
                    "api.max_order_quote_usdt_micros"
                )

    ingest = cfg.get("ingest") or {}
    if ingest and not isinstance(ingest, dict):
        errors.append("ingest must be a mapping")
    elif isinstance(ingest, dict):
        pw = ingest.get("parallel_workers")
        if pw is not None and (not isinstance(pw, int) or pw < 1 or pw > 32):
            errors.append("ingest.parallel_workers must be int 1..32")
        retries = ingest.get("http_retries")
        if retries is not None and (not isinstance(retries, int) or retries < 0 or retries > 5):
            errors.append("ingest.http_retries must be int 0..5")
        mresp = ingest.get("max_response_bytes")
        if mresp is not None and (
            not isinstance(mresp, int) or mresp < 65_536 or mresp > 67_108_864
        ):
            errors.append("ingest.max_response_bytes must be int 65536..67108864")
        for k in ("circuit_fail_threshold", "circuit_open_ms", "http_backoff_ms"):
            if k in ingest and (not isinstance(ingest[k], int) or ingest[k] <= 0):
                errors.append(f"ingest.{k} must be positive int")

    bubbles = cfg.get("bubbles")
    if bubbles is not None:
        if not isinstance(bubbles, dict):
            errors.append("bubbles must be a mapping")
        else:
            if "enabled" in bubbles and not isinstance(bubbles["enabled"], bool):
                errors.append("bubbles.enabled must be bool")
            tn = bubbles.get("top_n")
            if tn is not None and (not isinstance(tn, int) or tn < 1 or tn > 250):
                errors.append("bubbles.top_n must be int 1..250")
            ttl = bubbles.get("cache_ttl_ms")
            if ttl is not None and (
                not isinstance(ttl, int) or ttl < 5_000 or ttl > 600_000
            ):
                errors.append("bubbles.cache_ttl_ms must be int 5000..600000")

    if errors:
        joined = "; ".join(errors[:64])
        if len(joined) > 2048:
            joined = joined[:2048] + "…"
        raise ConfigError("config validation failed: " + joined)


def venue_priority(cfg: Dict[str, Any]) -> Dict[str, int]:
    """Lower index = higher priority (KuCoin first in default.yaml)."""
    venues = list(cfg.get("venues") or [])
    return {name: i for i, name in enumerate(venues)}
