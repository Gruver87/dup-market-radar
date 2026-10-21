"""Strict trade-symbol / clientOid / orderId validation (fail-closed)."""

from __future__ import annotations

import re
from typing import Optional

# Compact or dashed pair forms: BTCUSDT, BTC-USDT, 1000PEPE-USDT
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,30}[A-Z0-9]$")
# UUID / dashboard radar-* / dashboard client oids
_CLIENT_OID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{6,62}[A-Za-z0-9]$")
# KuCoin orderId — opaque token; allow -/_ ; refuse path/query injection
_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Spot base/quote currency for /accounts?currency=
_CURRENCY_RE = re.compile(r"^[A-Z0-9]{2,16}$")

SYMBOL_MIN_LEN = 3
SYMBOL_MAX_LEN = 32
CLIENT_OID_MIN_LEN = 8
CLIENT_OID_MAX_LEN = 64
ORDER_ID_MIN_LEN = 1
ORDER_ID_MAX_LEN = 64


class SymbolValidationError(ValueError):
    """Invalid trade symbol, clientOid, or orderId."""


def normalize_trade_symbol(symbol: str) -> str:
    """Uppercase / strip; refuse junk length or charset."""
    s = (symbol or "").strip().upper().replace(" ", "").replace("\\", "")
    if len(s) < SYMBOL_MIN_LEN or len(s) > SYMBOL_MAX_LEN:
        raise SymbolValidationError(
            f"symbol length must be {SYMBOL_MIN_LEN}..{SYMBOL_MAX_LEN}"
        )
    if not _SYMBOL_RE.fullmatch(s):
        raise SymbolValidationError("symbol charset refused")
    if ".." in s or "//" in s or "--" in s:
        raise SymbolValidationError("symbol format refused")
    return s


def validate_client_oid(client_oid: Optional[str]) -> Optional[str]:
    """Return cleaned oid or None if empty; raise on malformed."""
    if client_oid is None:
        return None
    s = str(client_oid).strip()
    if not s:
        return None
    if len(s) < CLIENT_OID_MIN_LEN or len(s) > CLIENT_OID_MAX_LEN:
        raise SymbolValidationError(
            f"client_oid length must be {CLIENT_OID_MIN_LEN}..{CLIENT_OID_MAX_LEN}"
        )
    if not _CLIENT_OID_RE.fullmatch(s):
        raise SymbolValidationError("client_oid charset refused")
    return s


def validate_order_id(order_id: str) -> str:
    """KuCoin orderId for signed GET /api/v1/orders/{id} — refuse path injection."""
    s = str(order_id or "").strip()
    if len(s) < ORDER_ID_MIN_LEN or len(s) > ORDER_ID_MAX_LEN:
        raise SymbolValidationError(
            f"order_id length must be {ORDER_ID_MIN_LEN}..{ORDER_ID_MAX_LEN}"
        )
    if any(c in s for c in "/\\?#%.+"):
        raise SymbolValidationError("order_id charset refused")
    if not _ORDER_ID_RE.fullmatch(s):
        raise SymbolValidationError("order_id charset refused")
    return s


def validate_currency(currency: str) -> str:
    """KuCoin account currency query — uppercase alnum only."""
    s = str(currency or "").strip().upper()
    if not _CURRENCY_RE.fullmatch(s):
        raise SymbolValidationError("currency charset/length refused")
    return s
