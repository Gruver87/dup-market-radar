"""Fiat FX — ECB (frankfurter) + NBRB (BYN/RUB) — integer micros, allowlisted HTTPS.

Frankfurter/ECB does not publish BYN or RUB. Belarus National Bank (NBRB) is the
fail-closed source for those; other pairs stay on frankfurter mid rates.
"""

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
NBRB_HOST = "www.nbrb.by"
FX_MAX_RESPONSE_BYTES = 65_536
# Fiat amount / rate scale: micros (1 unit = 1_000_000)
FX_MICROS = 1_000_000
_CCY_RE = re.compile(r"^[A-Z]{3}$")
# Currencies not on ECB frankfurter — route via NBRB (BYN book).
_NBRB_ONLY = frozenset({"BYN", "RUB"})
_CACHE_TTL_MS = 3_600_000  # 1h
_MAX_CACHE_BASES = 16
_lock = threading.Lock()
_cache: Dict[str, Tuple[int, Dict[str, int]]] = {}  # base -> (fetched_ms, quote->rate_micros)
_nbrb_cache: Optional[Tuple[int, Dict[str, int]]] = None  # (fetched_ms, ccy->BYN_micros_per_1_unit)
_circuit = VenueCircuitBreaker(fail_threshold=3, open_ms=60_000)
_nbrb_circuit = VenueCircuitBreaker(fail_threshold=3, open_ms=60_000)


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


def _fetch_nbrb_byn_book() -> Dict[str, int]:
    """Official NBRB rates → BYN micros per 1 unit of foreign currency.

    NBRB ``Cur_OfficialRate`` is BYN for ``Cur_Scale`` units of the foreign ccy.
    """
    url = f"https://{NBRB_HOST}/api/exrates/rates"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            verify=True,
            follow_redirects=False,
        ) as client:
            resp = client.get(
                url,
                params={"periodicity": "0"},
                headers={"User-Agent": "DUP-Market-Radar/0.2.65-fx-nbrb"},
            )
            resp.raise_for_status()
            data = read_capped_json(resp, FX_MAX_RESPONSE_BYTES, label="nbrb")
    except Exception as exc:  # noqa: BLE001
        raise FxError(redact_exc(exc, max_len=256)) from None
    if not isinstance(data, list):
        raise FxError("nbrb response not list")
    out: Dict[str, int] = {"BYN": FX_MICROS}
    for row in data[:256]:
        if not isinstance(row, dict):
            continue
        ccy = str(row.get("Cur_Abbreviation") or "").upper()
        if not _CCY_RE.fullmatch(ccy):
            continue
        scale = int(row.get("Cur_Scale") or 0)
        if scale <= 0:
            continue
        rate = _rate_to_micros(row.get("Cur_OfficialRate"))
        if rate is None:
            continue
        # BYN micros per 1 foreign unit = OfficialRate_micros / Scale
        per_unit = rate // scale
        if per_unit > 0:
            out[ccy] = per_unit
    if "USD" not in out:
        raise FxError("nbrb USD rate missing")
    return out


def get_nbrb_byn_book() -> Dict[str, int]:
    """Cached NBRB book: foreign → BYN micros per 1 unit."""
    global _nbrb_cache
    now = int(time.time() * 1000)
    with _lock:
        if _nbrb_cache and now - _nbrb_cache[0] < _CACHE_TTL_MS:
            return dict(_nbrb_cache[1])
    if not _nbrb_circuit.allow("nbrb"):
        with _lock:
            if _nbrb_cache:
                return dict(_nbrb_cache[1])
        raise FxError("nbrb circuit open — soft refuse")
    try:
        book = _fetch_nbrb_byn_book()
        _nbrb_circuit.record_ok("nbrb")
    except FxError as exc:
        safe = redact_exc(exc, max_len=256)
        _nbrb_circuit.record_fail("nbrb", reason=safe)
        with _lock:
            if _nbrb_cache:
                logger.warning("nbrb fetch failed, serving stale: %s", safe)
                return dict(_nbrb_cache[1])
        raise
    with _lock:
        _nbrb_cache = (now, dict(book))
    return book


def _to_usd_micros(amount_micros: int, ccy: str) -> Tuple[int, str]:
    """Convert amount in ``ccy`` micros → USD micros. Returns (usd_micros, source)."""
    if ccy == "USD":
        return amount_micros, "identity"
    if ccy in _NBRB_ONLY or ccy == "BYN":
        book = get_nbrb_byn_book()
        if ccy == "BYN":
            usd_per_byn = book.get("USD")
            if not usd_per_byn:
                raise FxError("nbrb USD missing")
            # 1 USD = usd_per_byn BYN micros → USD = BYN * FX / usd_per_byn
            return (amount_micros * FX_MICROS) // usd_per_byn, "nbrb"
        # RUB (etc.): amount → BYN → USD
        byn_per = book.get(ccy)
        usd_per_byn = book.get("USD")
        if not byn_per or not usd_per_byn:
            raise FxError(f"nbrb {ccy}/USD unavailable")
        byn_micros = (amount_micros * byn_per) // FX_MICROS
        return (byn_micros * FX_MICROS) // usd_per_byn, "nbrb"
    # ECB path: USD book has 1 USD = rate micros of ccy
    usd_rates = get_rates("USD")
    rate = usd_rates.get(ccy)
    if rate is None:
        raise FxError(f"frankfurter {ccy} unavailable")
    return (amount_micros * FX_MICROS) // rate, "frankfurter"


def _from_usd_micros(usd_micros: int, ccy: str) -> Tuple[int, str]:
    """Convert USD micros → ``ccy`` micros."""
    if ccy == "USD":
        return usd_micros, "identity"
    if ccy in _NBRB_ONLY or ccy == "BYN":
        book = get_nbrb_byn_book()
        if ccy == "BYN":
            usd_per_byn = book.get("USD")
            if not usd_per_byn:
                raise FxError("nbrb USD missing")
            return (usd_micros * usd_per_byn) // FX_MICROS, "nbrb"
        byn_per = book.get(ccy)
        usd_per_byn = book.get("USD")
        if not byn_per or not usd_per_byn:
            raise FxError(f"nbrb {ccy}/USD unavailable")
        byn_micros = (usd_micros * usd_per_byn) // FX_MICROS
        # 1 foreign = byn_per BYN → foreign = BYN * FX / byn_per
        return (byn_micros * FX_MICROS) // byn_per, "nbrb"
    usd_rates = get_rates("USD")
    rate = usd_rates.get(ccy)
    if rate is None:
        raise FxError(f"frankfurter {ccy} unavailable")
    return (usd_micros * rate) // FX_MICROS, "frankfurter"


def convert(
    amount_micros: int,
    from_ccy: str,
    to_ccy: str,
) -> Dict[str, Any]:
    """Convert integer micros (ECB mid and/or NBRB official) — integer math only."""
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
            "nbrb_circuit": _nbrb_circuit.status(),
        }

    needs_nbrb = src in _NBRB_ONLY or dst in _NBRB_ONLY or src == "BYN" or dst == "BYN"
    if needs_nbrb:
        usd_micros, src_a = _to_usd_micros(amount_micros, src)
        out_micros, src_b = _from_usd_micros(usd_micros, dst)
        rate_i = (out_micros * FX_MICROS) // amount_micros if amount_micros else 0
        parts = sorted({p for p in (src_a, src_b) if p != "identity"})
        source = "+".join(parts) if parts else "nbrb"
        if "nbrb" in source and "frankfurter" in source:
            source = "nbrb+frankfurter/USD-cross"
        elif source == "nbrb":
            source = "nbrb/USD-cross"
        return {
            "ok": True,
            "from": src,
            "to": dst,
            "amount_in_micros": amount_micros,
            "amount_out_micros": int(out_micros),
            "rate_micros": int(rate_i),
            "scale": FX_MICROS,
            "source": source,
            "circuit": _circuit.status(),
            "nbrb_circuit": _nbrb_circuit.status(),
        }

    rates = get_rates(src)
    rate_i = rates.get(dst)
    if rate_i is None:
        # Cross via USD if needed
        usd_rates = get_rates("USD")
        if src not in usd_rates or dst not in usd_rates:
            raise FxError(f"pair {src}/{dst} unavailable")
        rate_src = usd_rates[src]
        rate_dst = usd_rates[dst]
        usd_micros = (amount_micros * FX_MICROS) // rate_src
        out_micros = (usd_micros * rate_dst) // FX_MICROS
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
            "nbrb_circuit": _nbrb_circuit.status(),
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
        "nbrb_circuit": _nbrb_circuit.status(),
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
    src = str(result.get("source") or "")
    if "nbrb" in src:
        foot = "NBRB official + ECB mid · not financial advice" if lg == "en" else (
            "НБРБ офиц. + ECB mid · не финсовет"
        )
    else:
        foot = "ECB mid via frankfurter · not financial advice" if lg == "en" else (
            "ECB mid (frankfurter) · не финсовет"
        )
    return (
        f"<b>{title}</b>\n"
        f"<code>{_fmt(ain)} {result['from']}</code> → "
        f"<code>{_fmt(aout)} {result['to']}</code>\n"
        f"rate=<code>{_fmt(rate)}</code> · src={src}\n"
        f"<i>{foot}</i>"
    )


def ensure_fx_host_allowlisted(hosts: Set[str]) -> Set[str]:
    """Return copy of allowlist with FX hosts present (for ingest clients if shared)."""
    out = set(hosts)
    out.add(FX_HOST)
    out.add(NBRB_HOST)
    return out
