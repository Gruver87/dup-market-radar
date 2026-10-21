"""KuCoin spot symbol meta — minSize / minFunds gates (integer money)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from radar.money import base_i, quote_i
from radar.secrets_redact import redact_text
from radar.trade.env_load import kucoin_creds, validate_kucoin_base_url

logger = logging.getLogger("radar.trade.symbol_meta")

# Soft memory bound — KuCoin spot universe is << this; refuse absurd payloads.
_MAX_SYMBOL_META = 20_000


@dataclass(frozen=True)
class SymbolMeta:
    pair: str
    enable_trading: bool
    base_min_i: int
    min_funds_micros: int
    base_inc_i: int
    quote_min_micros: int = 0  # quoteMinSize — floor for market buy notional
    quote_inc_micros: int = 0  # quoteIncrement — snap buy funds

    def effective_buy_min_micros(self) -> int:
        """Strictest of minFunds and quoteMinSize (USDT micros)."""
        return max(int(self.min_funds_micros), int(self.quote_min_micros))

    def validate_buy(self, funds_micros: int) -> Optional[str]:
        if not self.enable_trading:
            return f"{self.pair} trading disabled"
        need = self.effective_buy_min_micros()
        if need > 0 and int(funds_micros) < need:
            return (
                f"buy funds {funds_micros} < minNotional {need} "
                f"(minFunds={self.min_funds_micros} quoteMin={self.quote_min_micros}) "
                f"({self.pair})"
            )
        return None

    def validate_sell(self, size_i: int) -> Optional[str]:
        if not self.enable_trading:
            return f"{self.pair} trading disabled"
        if self.base_min_i > 0 and int(size_i) < self.base_min_i:
            return f"sell size {size_i} < baseMinSize {self.base_min_i} ({self.pair})"
        return None

    def floor_size(self, size_i: int) -> int:
        """Truncate size to baseIncrement (toward zero)."""
        inc = int(self.base_inc_i)
        if inc <= 0:
            return int(size_i)
        return (int(size_i) // inc) * inc

    def floor_funds(self, funds_micros: int) -> int:
        """Truncate quote funds to quoteIncrement (toward zero)."""
        inc = int(self.quote_inc_micros)
        if inc <= 0:
            return int(funds_micros)
        return (int(funds_micros) // inc) * inc


def parse_symbol_row(row: Dict[str, Any]) -> Optional[SymbolMeta]:
    pair = str(row.get("symbol") or "").upper()
    if not pair or "-" not in pair:
        return None
    base_min = base_i(row.get("baseMinSize")) or 0
    min_funds = quote_i(row.get("minFunds")) or 0
    quote_min = quote_i(row.get("quoteMinSize")) or 0
    quote_inc = quote_i(row.get("quoteIncrement")) or 0
    base_inc = base_i(row.get("baseIncrement")) or 0
    enabled = bool(row.get("enableTrading", True))
    return SymbolMeta(
        pair=pair,
        enable_trading=enabled,
        base_min_i=int(base_min),
        min_funds_micros=int(min_funds),
        base_inc_i=int(base_inc),
        quote_min_micros=int(quote_min),
        quote_inc_micros=int(quote_inc),
    )


class SymbolMetaCache:
    """Public symbols map with TTL. Fail-closed on miss when require=True."""

    def __init__(
        self,
        *,
        ttl_sec: int = 3600,
        base_url: Optional[str] = None,
    ) -> None:
        self._ttl_sec = max(60, int(ttl_sec))
        if base_url is None:
            creds = kucoin_creds()
            self._base = str(creds.get("base_url") or "")
            self._base_error = str(creds.get("base_url_error") or "") or None
        else:
            safe, err = validate_kucoin_base_url(base_url)
            self._base = safe
            self._base_error = err
        self._lock = threading.Lock()
        self._by_pair: Dict[str, SymbolMeta] = {}
        self._fetched_at: int = 0  # epoch seconds
        self._last_error: Optional[str] = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            age = (
                int(time.time()) - self._fetched_at if self._fetched_at else None
            )
            return {
                "symbols": len(self._by_pair),
                "age_sec": age,
                "ttl_sec": self._ttl_sec,
                "base_url_ok": not bool(self._base_error) and bool(self._base),
                "base_url_error": self._base_error,
                "last_error": self._last_error,
            }

    def get(self, pair: str, *, refresh_if_stale: bool = True) -> Optional[SymbolMeta]:
        key = str(pair).upper()
        with self._lock:
            now = int(time.time())
            stale = (now - self._fetched_at) >= self._ttl_sec
            hit = self._by_pair.get(key)
            if hit and not stale:
                return hit
            need_fetch = refresh_if_stale and (stale or not self._by_pair)
        if need_fetch:
            self.refresh()
            with self._lock:
                return self._by_pair.get(key)
        return hit

    def refresh(self) -> int:
        if not self._base or self._base_error:
            self._last_error = self._base_error or "KUCOIN_API_BASE refused"
            logger.warning("symbol meta refresh refused: %s", self._last_error)
            return 0
        from radar.http_client import META_MAX_RESPONSE_BYTES, read_capped_json

        url = f"{self._base}/api/v2/symbols"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=5.0),
                verify=True,
                follow_redirects=False,
            ) as client:
                resp = client.get(
                    url,
                    headers={"User-Agent": "DUP-Market-Radar/0.2.57-meta"},
                )
                resp.raise_for_status()
                payload = read_capped_json(
                    resp, META_MAX_RESPONSE_BYTES, label="kucoin-meta"
                )
        except Exception as exc:  # noqa: BLE001
            self._last_error = redact_text(str(exc))[:256]
            logger.warning("symbol meta fetch failed: %s", self._last_error)
            return 0
        if str(payload.get("code")) != "200000":
            self._last_error = redact_text(
                f"code={payload.get('code')} msg={payload.get('msg')}"
            )[:256]
            logger.warning("symbol meta refused: %s", self._last_error)
            return 0
        data = payload.get("data")
        if not isinstance(data, list):
            self._last_error = "symbols data missing"
            return 0
        mapped: Dict[str, SymbolMeta] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            meta = parse_symbol_row(row)
            if meta:
                mapped[meta.pair] = meta
            if len(mapped) >= _MAX_SYMBOL_META:
                logger.warning(
                    "symbol meta truncated at max=%s (upstream rows=%s)",
                    _MAX_SYMBOL_META,
                    len(data) if isinstance(data, list) else "?",
                )
                break
        with self._lock:
            self._by_pair = mapped
            self._fetched_at = int(time.time())
            self._last_error = None
        logger.info("symbol meta refreshed n=%s", len(mapped))
        return len(mapped)
