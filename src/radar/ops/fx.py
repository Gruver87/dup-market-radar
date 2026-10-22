"""Fiat FX via ECB (frankfurter.app) — integer micros only, allowlisted HTTPS."""

from __future__ import annotations

import logging
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Set, Tuple

import httpx

from radar.http_client import read_capped_json
from radar.ingest.circuit import VenueCircuitBreaker
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.ops.fx")

FX_HOST = "api.frankfurter.app"
FX_MAX_RESPONSE_BYTES = 65_536
# Fiat amount / rate scale: micros (1 unit = 1_000_000)
FX_MICROS = 1_000_000
_CCY_RE = re.compile(r"^[A-Z]{3}$")
_CACHE_TTL_MS = 3_600_000  # 1h
_MAX_CACHE_BASES = 16
_lock = threading.Lock()
_cache: Dict[str, Tuple[int, Dict[str, int]]] = {}  # base -> (fetched_ms, quote->rate_micros)
_circuit = VenueCircuitBreaker(fail_threshold=3, open_ms=60_000)


class FxError(RuntimeError):
    pass


def validate_ccy(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not _CCY_RE.fullmatch(s):
        raise FxError("currency must be 3-letter ISO (A-Z)")
    return s


def parse_amount_to_micros(raw: str) -> int:
    """Parse human amount → integer micros (fail-closed, no float money path)."""
    s = str(raw or "").strip().replace(",", ".")
    if not s or len(s) > 24:
        raise FxError("amount refused")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise FxError("amount unparseable") from exc
    if d <= 0 or d > Decimal("1000000000"):
        raise FxError("amount out of range")
    micros = int((d * Decimal(FX_MICROS)).to_integral_value())
    if micros <= 0:
        raise FxError("amount too small")
    return micros


def _rate_to_micros(v: Any) -> Optional[int]:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    if d <= 0:
        return None
    return int((d * Decimal(FX_MICROS)).to_integral_value())


def _fetch_rates(base: str) -> Dict[str, int]:
    url = f"https://{FX_HOST}/latest"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            verify=True,
            follow_redirects=False,
        ) as client:
            resp = client.get(
                url,
                params={"from": base},
                headers={"User-Agent": "DUP-Market-Radar/0.2.65-fx"},
            )
            resp.raise_for_status()
            data = read_capped_json(resp, FX_MAX_RESPONSE_BYTES, label="fx")
    except Exception as exc:  # noqa: BLE001
        raise FxError(redact_exc(exc, max_len=256)) from None
    if not isinstance(data, dict):
        raise FxError("fx response not object")
    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise FxError("fx rates missing")
    out: Dict[str, int] = {base: FX_MICROS}
    # Soft cap currency book size (ECB set is ~30; refuse absurd dumps)
    _MAX_RATES = 256
    for k, v in rates.items():
        if len(out) >= _MAX_RATES:
            break
        ccy = str(k).upper()
        if not _CCY_RE.fullmatch(ccy):
            continue
        ri = _rate_to_micros(v)
        if ri is not None:
            out[ccy] = ri
    if len(out) < 2:
        raise FxError("fx rates empty")
    return out


def get_rates(base: str = "USD") -> Dict[str, int]:
    base = validate_ccy(base)
    now = int(time.time() * 1000)
    with _lock:
        hit = _cache.get(base)
        if hit and now - hit[0] < _CACHE_TTL_MS:
            return dict(hit[1])
    if not _circuit.allow("frankfurter"):
        with _lock:
            # Soft: any cached base usable for cross; prefer requested base
            hit = _cache.get(base)
            if hit:
                return dict(hit[1])
            # Fall back to any cached book (USD typical)
            for _b, (_ts, rates) in _cache.items():
                return dict(rates)
        raise FxError("frankfurter circuit open — soft refuse")
    try:
        rates = _fetch_rates(base)
        _circuit.record_ok("frankfurter")
    except FxError as exc:
        safe = redact_exc(exc, max_len=256)
        _circuit.record_fail("frankfurter", reason=safe)
        with _lock:
            hit = _cache.get(base)
            if hit:
                logger.warning("fx fetch failed, serving stale: %s", safe)
                return dict(hit[1])
        raise
    with _lock:
        _cache[base] = (now, dict(rates))
        while len(_cache) > _MAX_CACHE_BASES:
            # Drop oldest by fetch timestamp
            oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
            if oldest == base:
                break
            _cache.pop(oldest, None)
    return rates


def convert(
    amount_micros: int,
    from_ccy: str,
    to_ccy: str,
) -> Dict[str, Any]:
    """Convert integer micros using ECB mid rates (integer math)."""
    if not isinstance(amount_micros, int) or amount_micros <= 0:
        raise FxError("amount_micros must be positive int")
    src = validate_ccy(from_ccy)
    dst = validate_ccy(to_ccy)
    if src == dst:
        return {
            "ok": True,
            "from": src,
            "to": dst,
            "amount_in_micros": amount_micros,
            "amount_out_micros": amount_micros,
            "rate_micros": FX_MICROS,
            "scale": FX_MICROS,
            "source": "identity",
            "circuit": _circuit.status(),
        }
    rates = get_rates(src)
    rate_i = rates.get(dst)
    if rate_i is None:
        # Cross via USD if needed
        usd_rates = get_rates("USD")
        if src not in usd_rates or dst not in usd_rates:
            raise FxError(f"pair {src}/{dst} unavailable")
        # out = amount * (USD/src)^-1 * (USD/dst) … rates from USD base: 1 USD = r micros of ccy
        # amount_src * (usd_per_src) / FX * dst_per_usd
        # If rates are USD->X: 1 USD = rate_x micros of X
        # amount in SRC micros → USD micros = amount * FX_MICROS / rate_src
        # then to DST = usd * rate_dst / FX_MICROS
        rate_src = usd_rates[src]
        rate_dst = usd_rates[dst]
        usd_micros = (amount_micros * FX_MICROS) // rate_src
        out_micros = (usd_micros * rate_dst) // FX_MICROS
        # Effective rate src→dst in micros: out/in * scale
        rate_i = (out_micros * FX_MICROS) // amount_micros if amount_micros else 0
        return {
            "ok": True,
            "from": src,
            "to": dst,
            "amount_in_micros": amount_micros,
            "amount_out_micros": int(out_micros),
            "rate_micros": int(rate_i),
            "scale": FX_MICROS,
            "source": "frankfurter/USD-cross",
            "circuit": _circuit.status(),
        }
    out_micros = (amount_micros * rate_i) // FX_MICROS
    return {
        "ok": True,
        "from": src,
        "to": dst,
        "amount_in_micros": amount_micros,
        "amount_out_micros": int(out_micros),
        "rate_micros": int(rate_i),
        "scale": FX_MICROS,
        "source": "frankfurter",
        "circuit": _circuit.status(),
    }


def format_fx_html(result: Dict[str, Any], *, lang: str = "en") -> str:
    from radar.i18n import normalize_lang

    lg = normalize_lang(lang)
    ain = int(result["amount_in_micros"])
    aout = int(result["amount_out_micros"])
    scale = int(result["scale"])
    rate = int(result["rate_micros"])

    def _fmt(micros: int) -> str:
        whole = micros // scale
        frac = abs(micros) % scale
        return f"{whole}.{frac:06d}".rstrip("0").rstrip(".")

    title = "FX" if lg == "en" else "Курс"
    return (
        f"<b>{title}</b>\n"
        f"<code>{_fmt(ain)} {result['from']}</code> → "
        f"<code>{_fmt(aout)} {result['to']}</code>\n"
        f"rate=<code>{_fmt(rate)}</code> · src={result.get('source')}\n"
        f"<i>ECB mid via frankfurter · not financial advice</i>"
    )


def ensure_fx_host_allowlisted(hosts: Set[str]) -> Set[str]:
    """Return copy of allowlist with FX host present (for ingest clients if shared)."""
    out = set(hosts)
    out.add(FX_HOST)
    return out
