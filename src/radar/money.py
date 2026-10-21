"""Integer fixed-point money (L1 satoshi discipline).

No float for money / scores / pct. Decimal is only used at the exchange
string boundary, then values are stored as int.

Scales
------
PRICE_SCALE  1e8   — price (like satoshi)
QUOTE_SCALE  1e6   — USDT micros (1 USDT = 1_000_000)
BASE_SCALE   1e8   — base asset size
BPS          1e2 per 1% — change 8.5% → 850 bps
SCORE_CENTI  1e2   — score 77.46 → 7746
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Optional

PRICE_SCALE = 100_000_000
QUOTE_SCALE = 1_000_000
BASE_SCALE = 100_000_000
# 1 percent = 100 bps
BPS_PER_PERCENT = 100
SCORE_CENTI = 100  # score points * 100
# Soft refuse absurd numeric strings (Decimal DoS / memory).
_MAX_DEC_CHARS = 64


class MoneyError(ValueError):
    pass


def _dec(v: Any) -> Decimal:
    if v is None or v == "":
        raise MoneyError("empty")
    if isinstance(v, bool):
        raise MoneyError("bool")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    # str / float-from-json: go through str to avoid binary float math in logic
    s = str(v).strip()
    if len(s) > _MAX_DEC_CHARS:
        raise MoneyError("decimal string too long")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"bad decimal: {s[:32]!r}") from exc


def to_scaled_int(v: Any, scale: int) -> Optional[int]:
    """Parse exchange/JSON number → scaled int. None if missing."""
    if v is None or v == "":
        return None
    try:
        d = _dec(v)
    except MoneyError:
        return None
    return int((d * Decimal(scale)).to_integral_value(rounding=ROUND_DOWN))


def price_i(v: Any) -> Optional[int]:
    return to_scaled_int(v, PRICE_SCALE)


def quote_i(v: Any) -> Optional[int]:
    """USDT → micros."""
    return to_scaled_int(v, QUOTE_SCALE)


def base_i(v: Any) -> Optional[int]:
    return to_scaled_int(v, BASE_SCALE)


def pct_to_bps(v: Any) -> Optional[int]:
    """Percent (e.g. 8.5) → basis points (850)."""
    if v is None or v == "":
        return None
    try:
        d = _dec(v)
    except MoneyError:
        return None
    return int((d * Decimal(BPS_PER_PERCENT)).to_integral_value(rounding=ROUND_DOWN))


def fraction_to_bps(v: Any) -> Optional[int]:
    """Fraction (e.g. 0.085 = 8.5%) → bps (850)."""
    if v is None or v == "":
        return None
    try:
        d = _dec(v)
    except MoneyError:
        return None
    return int((d * Decimal(10_000)).to_integral_value(rounding=ROUND_DOWN))


def score_to_centi(v: Any) -> int:
    if v is None:
        return 0
    try:
        d = _dec(v)
    except MoneyError:
        return 0
    return int((d * Decimal(SCORE_CENTI)).to_integral_value(rounding=ROUND_DOWN))


def scaled_to_str(n: int, scale: int, max_frac: int = 10) -> str:
    """Int → decimal string for exchange APIs (no float)."""
    if scale <= 0:
        raise MoneyError("scale")
    neg = n < 0
    n = abs(int(n))
    whole = n // scale
    frac = n % scale
    if frac == 0:
        s = str(whole)
    else:
        frac_s = str(frac).zfill(len(str(scale - 1)))[:max_frac].rstrip("0")
        s = f"{whole}.{frac_s}" if frac_s else str(whole)
    return f"-{s}" if neg and s != "0" else s


def price_str(n: int) -> str:
    return scaled_to_str(n, PRICE_SCALE)


def quote_str(n: int) -> str:
    return scaled_to_str(n, QUOTE_SCALE, max_frac=6)


def base_str(n: int) -> str:
    return scaled_to_str(n, BASE_SCALE)


def mul_div(a: int, b: int, denom: int) -> int:
    """(a * b) // denom with Python big int (exact truncate toward 0)."""
    if denom == 0:
        raise MoneyError("div0")
    return (int(a) * int(b)) // int(denom)


def dec_product_str(a: Any, b: Any) -> Optional[str]:
    """Decimal multiply at exchange boundary → string for later to_scaled_int."""
    try:
        return str(_dec(a) * _dec(b))
    except MoneyError:
        return None


def notional_i_from_price_qty(price_i_val: int, qty_i_val: int) -> int:
    """USDT micros from price_i (1e8) * qty_i (1e8) → quote micros (1e6)."""
    # (p/1e8)*(q/1e8)*1e6 = p*q / 1e10
    return mul_div(int(price_i_val), int(qty_i_val), 10_000_000_000)


def peg_deviation_bps_from_price(price_i_val: int, peg_i: int = PRICE_SCALE) -> int:
    """|price - peg| in bps of peg ($1 default)."""
    if peg_i <= 0:
        return 0
    return mul_div(abs(int(price_i_val) - int(peg_i)), 10_000, peg_i)


def apply_fee_bps(amount: int, fee_bps: int = 30) -> int:
    """Subtract fee in bps (30 = 0.30%)."""
    return mul_div(amount, 10_000 - fee_bps, 10_000)


def drop_bps(entry_price_i: int, mark_price_i: int) -> int:
    """How many bps price fell from entry (positive = drop)."""
    if entry_price_i <= 0:
        return 0
    return mul_div(entry_price_i - mark_price_i, 10_000, entry_price_i)


def display_price(price_i_val: Optional[int]) -> str:
    if price_i_val is None:
        return "—"
    return price_str(price_i_val)


def display_quote(quote_i_val: Optional[int]) -> str:
    if quote_i_val is None:
        return "—"
    # compact: micros → human
    return quote_str(quote_i_val)


def display_bps(bps: Optional[int]) -> str:
    if bps is None:
        return "—"
    # 850 bps → 8.50%
    sign = "+" if bps > 0 else ""
    whole = abs(bps) // 100
    frac = abs(bps) % 100
    core = f"{whole}.{frac:02d}%"
    if bps < 0:
        return f"-{core}"
    return f"{sign}{core}"


def display_score_centi(centi: Optional[int]) -> str:
    if centi is None:
        return "—"
    whole = centi // 100
    frac = abs(centi) % 100
    return f"{whole}.{frac:02d}"
