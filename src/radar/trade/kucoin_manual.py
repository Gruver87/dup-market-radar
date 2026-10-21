"""KuCoin spot market orders — integer micros/base only; confirm=True required."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from radar.money import BASE_SCALE, QUOTE_SCALE, base_i, base_str, quote_i, quote_str
from radar.trade.env_load import kucoin_creds, trading_configured
from radar.trade.oid_ledger import ClientOidLedger
from radar.trade.stats import TRADE_STATS
from radar.config import load_config
from radar.secrets_redact import redact_exc, redact_text
from radar.trade.symbol_meta import SymbolMetaCache

logger = logging.getLogger("radar.trade.kucoin")


@dataclass
class TradeRequest:
    symbol: str
    side: str  # buy | sell
    confirm: bool
    quote_usdt_micros: Optional[int] = None  # buy: USDT * 1e6
    base_size_i: Optional[int] = None  # sell: base * 1e8
    client_oid: Optional[str] = None


_MAX_MSG_CHARS = 512


@dataclass
class TradeResult:
    ok: bool
    dry_run: bool
    message: str
    request: Dict[str, Any]
    exchange: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.message is not None and len(self.message) > _MAX_MSG_CHARS:
            self.message = str(self.message)[:_MAX_MSG_CHARS]


@dataclass
class FillInfo:
    """Reconciled fill from GET /api/v1/orders/{id} — integer money only."""

    ok: bool
    order_id: str
    base_size_i: int = 0
    deal_funds_micros: int = 0
    fee_base_i: int = 0
    size_basis: str = "unreconciled"
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.message and len(self.message) > _MAX_MSG_CHARS:
            self.message = str(self.message)[:_MAX_MSG_CHARS]
        if self.order_id and len(self.order_id) > 64:
            self.order_id = str(self.order_id)[:64]
        if self.size_basis and len(self.size_basis) > 64:
            self.size_basis = str(self.size_basis)[:64]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "order_id": self.order_id,
            "base_size_i": self.base_size_i,
            "deal_funds_micros": self.deal_funds_micros,
            "fee_base_i": self.fee_base_i,
            "size_basis": self.size_basis,
            "message": self.message,
        }


def to_kucoin_symbol(symbol: str) -> str:
    from radar.trade.symbol_validate import normalize_trade_symbol

    s = normalize_trade_symbol(symbol)
    # Keep dash form; strip underscore/slash only for compact mapping
    compact = s.replace("_", "").replace("/", "")
    if "-" in compact:
        # already pair-like (e.g. BTC-USDT) — re-check after normalize
        return compact
    for quote in ("USDT", "USDC", "BTC", "ETH", "KCS"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[: -len(quote)]}-{quote}"
    raise ValueError(f"cannot map symbol to KuCoin pair: {str(symbol)[:32]!r}")


def resolve_base_asset(symbol: str) -> str:
    """Base currency for fee accounting. Empty string + warning on map failure."""
    try:
        pair = to_kucoin_symbol(symbol)
        return pair.split("-", 1)[0]
    except ValueError as exc:
        logger.warning(
            "base_asset resolve failed for %r: %s",
            str(symbol)[:32],
            redact_exc(exc, max_len=256),
        )
        return ""


def sanitize_exchange_surface(
    exchange: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Public/API/audit surface: code + msg + orderId only — no raw KuCoin blob."""
    if exchange is None:
        return None
    if not isinstance(exchange, dict):
        return None
    out: Dict[str, Any] = {
        "code": str(exchange.get("code") or "")[:32],
        "msg": redact_text(str(exchange.get("msg") or ""))[:256],
    }
    oid = extract_order_id(exchange)
    if oid:
        out["data"] = {"orderId": oid}
    return out


def sanitize_trade_request(req: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Public trade preview — allowlisted keys only; string caps."""
    if not isinstance(req, dict):
        return {}
    body_in = req.get("body")
    body_out: Dict[str, Any] = {}
    if isinstance(body_in, dict):
        for k in ("clientOid", "side", "symbol", "type", "funds", "size"):
            if k not in body_in:
                continue
            v = body_in[k]
            if isinstance(v, str):
                body_out[k] = v[:64]
            elif isinstance(v, (int, bool)) or v is None:
                body_out[k] = v
            else:
                body_out[k] = str(v)[:64]
    out: Dict[str, Any] = {
        "venue": str(req.get("venue") or "")[:32],
        "market": str(req.get("market") or "")[:16],
        "order_type": str(req.get("order_type") or "")[:16],
        "symbol": str(req.get("symbol") or "")[:32],
        "side": str(req.get("side") or "")[:8],
        "body": body_out,
        "integer_money": bool(req.get("integer_money")),
    }
    if req.get("client_oid") is not None:
        out["client_oid"] = str(req.get("client_oid") or "")[:64]
    if req.get("funds_floor") is not None:
        out["funds_floor"] = str(req.get("funds_floor"))[:128]
    if req.get("symbol_meta_refuse") is not None:
        out["symbol_meta_refuse"] = str(req.get("symbol_meta_refuse"))[:256]
    return out


def extract_order_id(exchange: Optional[Dict[str, Any]]) -> Optional[str]:
    from radar.trade.symbol_validate import SymbolValidationError, validate_order_id

    if not exchange or not isinstance(exchange, dict):
        return None
    raw: Optional[str] = None
    data = exchange.get("data")
    if isinstance(data, dict):
        oid = data.get("orderId") or data.get("order_id") or data.get("id")
        if oid:
            raw = str(oid)
    elif isinstance(data, str) and data:
        raw = data
    if not raw:
        return None
    try:
        return validate_order_id(raw)
    except SymbolValidationError:
        logger.warning("exchange order_id refused by charset/length gate")
        return None


def parse_order_fill(order: Dict[str, Any], *, base_asset: str = "") -> FillInfo:
    """Map KuCoin order payload → net sellable base_size_i.

    If feeCurrency is the base asset, subtract fee from dealSize (sellable bag).
    """
    oid = str(order.get("id") or order.get("orderId") or "")
    deal_size = base_i(order.get("dealSize"))
    deal_funds = quote_i(order.get("dealFunds"))
    if deal_size is None or deal_size <= 0:
        return FillInfo(
            ok=False,
            order_id=oid,
            message="dealSize missing or zero",
        )
    fee_raw = order.get("fee")
    fee_ccy = str(order.get("feeCurrency") or "").upper()
    symbol = str(order.get("symbol") or "")
    if not base_asset and "-" in symbol:
        base_asset = symbol.split("-", 1)[0]
    fee_base = 0
    if fee_raw is not None and fee_ccy and base_asset and fee_ccy == base_asset.upper():
        fb = base_i(fee_raw)
        fee_base = int(fb or 0)
    net = int(deal_size) - fee_base
    if net <= 0:
        return FillInfo(
            ok=False,
            order_id=oid,
            base_size_i=0,
            deal_funds_micros=int(deal_funds or 0),
            fee_base_i=fee_base,
            message="net base_size_i <= 0 after fee",
        )
    return FillInfo(
        ok=True,
        order_id=oid,
        base_size_i=net,
        deal_funds_micros=int(deal_funds or 0),
        fee_base_i=fee_base,
        size_basis="kucoin_fill",
        message="reconciled",
    )


class KuCoinManualTrader:
    def __init__(
        self,
        ledger: Optional[ClientOidLedger] = None,
        *,
        sell_clamp: bool = True,
        symbol_meta: Optional[SymbolMetaCache] = None,
        enforce_symbol_meta: bool = True,
    ) -> None:
        self._creds = kucoin_creds()
        self._ledger = ledger
        self._sell_clamp = bool(sell_clamp)
        self._symbol_meta = symbol_meta if symbol_meta is not None else SymbolMetaCache()
        self._enforce_symbol_meta = bool(enforce_symbol_meta)
    def refresh_creds(self) -> None:
        self._creds = kucoin_creds()

    @property
    def ready(self) -> bool:
        self.refresh_creds()
        return trading_configured()

    def status(self) -> Dict[str, Any]:
        ready = self.ready
        out: Dict[str, Any] = {
            "venue": "kucoin",
            "manual_buttons": True,
            "configured": ready,
            "money": "integer_fixed_point",
            "quote_scale": QUOTE_SCALE,
            "base_scale": BASE_SCALE,
            "base_url": self._creds.get("base_url") or None,
            "base_url_ok": not bool(self._creds.get("base_url_error")),
            "fill_reconcile": True,
            "sell_clamp_to_balance": self._sell_clamp,
            "client_oid_ledger": True if self._ledger else False,
            "enforce_symbol_meta": self._enforce_symbol_meta,
            "symbol_meta": self._symbol_meta.status(),
        }
        if self._creds.get("base_url_error"):
            out["base_url_error"] = self._creds["base_url_error"]
        if self._ledger:
            out["oid_ledger"] = self._ledger.status()
        return out

    def place(self, req: TradeRequest) -> TradeResult:
        self.refresh_creds()
        if self._creds.get("base_url_error") or not self._creds.get("base_url"):
            return TradeResult(
                False,
                True,
                f"KuCoin base URL refused: "
                f"{self._creds.get('base_url_error') or 'empty'}",
                {},
            )
        side = (req.side or "").lower().strip()
        if side not in {"buy", "sell"}:
            return TradeResult(False, True, "side must be buy or sell", {})

        try:
            pair = to_kucoin_symbol(req.symbol)
        except ValueError as exc:
            return TradeResult(False, True, redact_exc(exc, max_len=256), {})

        try:
            from radar.trade.symbol_validate import validate_client_oid

            cleaned_oid = validate_client_oid(req.client_oid)
        except ValueError as exc:
            return TradeResult(False, True, redact_exc(exc, max_len=256), {})
        client_oid = str(cleaned_oid or uuid.uuid4())
        clamp_note: Optional[str] = None

        funds_floor_note: Optional[str] = None
        if side == "buy":
            micros = req.quote_usdt_micros
            if micros is None or int(micros) <= 0:
                return TradeResult(False, True, "buy requires quote_usdt_micros > 0 (int)", {})
            micros = int(micros)
            max_q = int(
                (load_config().get("api") or {}).get("max_order_quote_usdt_micros")
                or 500_000_000
            )
            if micros > max_q:
                return TradeResult(
                    False,
                    True,
                    f"buy funds {micros} > max_order_quote_usdt_micros {max_q}",
                    {"symbol": pair, "side": side},
                )
            # Snap funds to quoteIncrement before minNotional check
            if self._enforce_symbol_meta:
                meta_early = self._symbol_meta.get(pair)
                if meta_early is not None:
                    floored_f = meta_early.floor_funds(micros)
                    if floored_f != micros:
                        funds_floor_note = f"funds floored {micros}→{floored_f}"
                        micros = floored_f
                    if micros <= 0:
                        return TradeResult(
                            False,
                            True,
                            f"buy funds after quoteIncrement floor=0 ({pair})",
                            {"symbol": pair, "side": side},
                        )
            body: Dict[str, Any] = {
                "clientOid": client_oid,
                "side": "buy",
                "symbol": pair,
                "type": "market",
                "funds": quote_str(int(micros)),
            }
        else:
            size_i = req.base_size_i
            if size_i is None or int(size_i) <= 0:
                return TradeResult(False, True, "sell requires base_size_i > 0 (int)", {})
            size_i = int(size_i)
            max_b = int(
                (load_config().get("api") or {}).get("max_order_base_size_i")
                or 100_000_000_000_000
            )
            if size_i > max_b:
                return TradeResult(
                    False,
                    True,
                    f"sell size {size_i} > max_order_base_size_i {max_b}",
                    {"symbol": pair, "side": side},
                )
            body = {
                "clientOid": client_oid,
                "side": "sell",
                "symbol": pair,
                "type": "market",
                "size": base_str(size_i),
            }

        preview = {
            "venue": "kucoin",
            "market": "spot",
            "order_type": "market",
            "symbol": pair,
            "side": side,
            "body": body,
            "integer_money": True,
            "client_oid": client_oid,
        }
        if funds_floor_note:
            preview["funds_floor"] = funds_floor_note

        # Symbol meta gate (preview + live) — soft refuse under mins
        meta_err = self._check_symbol_meta(pair, side, body)
        if meta_err:
            preview["symbol_meta_refuse"] = meta_err
            if not req.confirm:
                TRADE_STATS.inc("place_preview")
                return TradeResult(False, True, meta_err, preview)
            TRADE_STATS.inc("place_fail")
            return TradeResult(False, True, meta_err, preview)

        if not req.confirm:
            TRADE_STATS.inc("place_preview")
            return TradeResult(
                True,
                True,
                "preview only — set confirm=true to place order on KuCoin",
                preview,
            )

        if not trading_configured():
            return TradeResult(
                False,
                True,
                "KuCoin API keys missing — set KUCOIN_API_KEY / SECRET / PASSPHRASE in .env",
                preview,
            )

        # Sell clamp to trade-account available (fail-closed if zero)
        if side == "sell" and self._sell_clamp:
            base_ccy = pair.split("-", 1)[0]
            try:
                avail_i = self.get_available_base_i(base_ccy)
            except Exception as exc:  # noqa: BLE001
                safe = redact_exc(exc, max_len=256)
                # No exc_info — signed/balance URLs must not hit traceback dumps
                logger.error("balance fetch failed: %s", safe)
                TRADE_STATS.inc("place_fail")
                return TradeResult(
                    False,
                    False,
                    f"sell clamp: cannot read balance for {base_ccy}: {safe}",
                    preview,
                )
            if avail_i <= 0:
                TRADE_STATS.inc("place_fail")
                return TradeResult(
                    False,
                    False,
                    f"sell clamp: available {base_ccy}=0 — refuse",
                    preview,
                )
            req_size = int(size_i)
            sized = min(req_size, avail_i)
            meta = self._symbol_meta.get(pair) if self._enforce_symbol_meta else None
            if meta:
                floored = meta.floor_size(sized)
                if floored != sized:
                    sized = floored
                    preview["floored_base_size_i"] = sized
            if sized <= 0 or (meta and meta.validate_sell(sized)):
                TRADE_STATS.inc("place_fail")
                err = (
                    meta.validate_sell(sized)
                    if meta and meta.validate_sell(sized)
                    else f"sell size after clamp/floor={sized} — refuse"
                )
                return TradeResult(False, False, err or "sell size refuse", preview)
            if sized != req_size:
                body["size"] = base_str(sized)
                preview["body"] = body
                preview["requested_base_size_i"] = req_size
                preview["clamped_base_size_i"] = sized
                clamp_note = f"clamped {req_size}→{sized}"
                TRADE_STATS.inc("sell_clamped")
                size_i = sized

        if self._ledger is not None:
            try:
                claimed = self._ledger.claim(client_oid, side=side, symbol=pair)
            except Exception as exc:  # noqa: BLE001
                TRADE_STATS.inc("place_fail")
                return TradeResult(
                    False,
                    True,
                    f"clientOid ledger persist failed: {redact_exc(exc, max_len=256)}",
                    preview,
                )
            if not claimed:
                TRADE_STATS.inc("duplicate_oid")
                return TradeResult(
                    False,
                    True,
                    f"duplicate clientOid refused: {client_oid}",
                    preview,
                )

        try:
            exchange = self._signed_post("/api/v1/orders", body)
        except Exception as exc:  # noqa: BLE001
            safe = redact_exc(exc, max_len=256)
            # No exc_info — signed request URLs/headers must not hit traceback dumps
            logger.error("kucoin order failed: %s", safe)
            # Keep oid claimed — uncertain whether exchange saw the POST
            TRADE_STATS.inc("place_fail")
            return TradeResult(False, False, f"order failed: {safe}", preview)

        ok = str(exchange.get("code")) == "200000"
        if ok:
            TRADE_STATS.inc("place_ok")
            if self._ledger is not None:
                self._ledger.mark_accepted(
                    client_oid, order_id=extract_order_id(exchange)
                )
            msg = "order accepted" + (f" ({clamp_note})" if clamp_note else "")
        else:
            TRADE_STATS.inc("place_fail")
            if self._ledger is not None:
                self._ledger.release(client_oid)
            raw_msg = exchange.get("msg")
            if raw_msg is None or raw_msg == "":
                msg = f"exchange refused: code={exchange.get('code')}"
            else:
                msg = f"exchange refused: {redact_text(str(raw_msg))[:256]}"
        return TradeResult(ok, False, msg, preview, exchange)

    def _check_symbol_meta(
        self, pair: str, side: str, body: Dict[str, Any]
    ) -> Optional[str]:
        if not self._enforce_symbol_meta:
            return None
        meta = self._symbol_meta.get(pair)
        if meta is None:
            return f"symbol meta unavailable for {pair} — refuse"
        if side == "buy":
            micros = quote_i(body.get("funds"))
            if micros is None or micros <= 0:
                return "buy funds unparseable for meta gate"
            return meta.validate_buy(int(micros))
        size = base_i(body.get("size"))
        if size is None or size <= 0:
            return "sell size unparseable for meta gate"
        return meta.validate_sell(int(size))

    def get_spot_mark_price_i(self, symbol: str) -> Optional[int]:
        """Public KuCoin level1 last → PRICE_SCALE. Fail-closed: None on any error."""
        from radar.money import price_i as to_price_i

        pair = to_kucoin_symbol(symbol)
        self.refresh_creds()
        base = str(self._creds.get("base_url") or "https://api.kucoin.com").rstrip("/")
        if not base.startswith("https://"):
            logger.warning("mark price refused: non-https base_url")
            return None
        path = f"/api/v1/market/orderbook/level1?symbol={pair}"
        from radar.http_client import SIGNED_MAX_RESPONSE_BYTES, read_capped_json

        try:
            with self._signed_client() as client:
                resp = client.get(f"{base}{path}", headers={
                    "User-Agent": "DUP-Market-Radar/0.2.64-manual",
                })
                resp.raise_for_status()
                body = read_capped_json(
                    resp, SIGNED_MAX_RESPONSE_BYTES, label="kucoin-level1"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mark price fetch failed %s: %s",
                pair,
                redact_exc(exc, max_len=128),
            )
            return None
        if str(body.get("code")) != "200000":
            return None
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        pi = to_price_i(data.get("price"))
        if pi is None or int(pi) <= 0:
            return None
        return int(pi)

    def get_available_base_i(self, currency: str) -> int:
        """Trade-account available balance → base_size_i (1e8)."""
        from radar.trade.symbol_validate import SymbolValidationError, validate_currency

        self.refresh_creds()
        if not trading_configured():
            raise RuntimeError("KuCoin keys missing")
        try:
            ccy = validate_currency(currency)
        except SymbolValidationError as exc:
            raise ValueError(redact_exc(exc, max_len=256)) from None
        resp = self._signed_get("/api/v1/accounts", {"currency": ccy, "type": "trade"})
        if str(resp.get("code")) != "200000":
            code = str(resp.get("code") or "")[:32]
            msg = redact_text(str(resp.get("msg") or ""))[:256]
            raise RuntimeError(f"accounts refused: code={code} msg={msg}")
        data = resp.get("data")
        if not isinstance(data, list) or not data:
            return 0
        # Sum available across matching trade accounts
        total = 0
        for acct in data:
            if not isinstance(acct, dict):
                continue
            if str(acct.get("currency") or "").upper() != ccy:
                continue
            ai = base_i(acct.get("available"))
            if ai:
                total += int(ai)
        return total

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """GET /api/v1/orders/{orderId} — signed."""
        from radar.trade.symbol_validate import SymbolValidationError, validate_order_id

        self.refresh_creds()
        if not trading_configured():
            raise RuntimeError("KuCoin keys missing")
        try:
            oid = validate_order_id(order_id)
        except SymbolValidationError as exc:
            raise ValueError(redact_exc(exc, max_len=256)) from None
        return self._signed_get(f"/api/v1/orders/{oid}")

    def reconcile_fill(
        self,
        order_id: str,
        *,
        max_attempts: int = 8,
        delay_ms: int = 250,
        base_asset: str = "",
    ) -> FillInfo:
        """Poll order until dealSize > 0 or attempts exhausted."""
        from radar.trade.symbol_validate import SymbolValidationError, validate_order_id

        try:
            oid = validate_order_id(order_id)
        except SymbolValidationError as exc:
            return FillInfo(ok=False, order_id="", message=redact_exc(exc, max_len=256))
        last_err = "no attempt"
        for attempt in range(max(1, int(max_attempts))):
            try:
                resp = self.get_order(oid)
            except Exception as exc:  # noqa: BLE001
                last_err = redact_exc(exc, max_len=256)
                logger.warning(
                    "fill poll %s attempt %s: %s", oid, attempt + 1, last_err
                )
                time.sleep(max(0, delay_ms) / 1000.0)
                continue
            if str(resp.get("code")) != "200000":
                last_err = redact_text(
                    f"exchange code={resp.get('code')} msg={resp.get('msg')}"
                )[:256]
                time.sleep(max(0, delay_ms) / 1000.0)
                continue
            data = resp.get("data")
            if not isinstance(data, dict):
                last_err = "order data missing"
                time.sleep(max(0, delay_ms) / 1000.0)
                continue
            fill = parse_order_fill(data, base_asset=base_asset)
            if fill.ok:
                TRADE_STATS.inc("fills_ok")
                return fill
            last_err = fill.message or "unfilled"
            # still active / not filled yet
            if data.get("isActive") is True or str(data.get("dealSize") or "0") in {
                "0",
                "0.0",
                "",
            }:
                time.sleep(max(0, delay_ms) / 1000.0)
                continue
            return fill
        TRADE_STATS.inc("fills_fail")
        return FillInfo(ok=False, order_id=oid, message=f"reconcile timeout: {last_err}")

    def _auth_headers(self, method: str, path_with_query: str, body: str = "") -> Dict[str, str]:
        key = self._creds["api_key"]
        secret = self._creds["api_secret"]
        passphrase = self._creds["api_passphrase"]
        ts = str(int(time.time() * 1000))
        prehash = f"{ts}{method}{path_with_query}{body}"
        sign = base64.b64encode(
            hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        pp = base64.b64encode(
            hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "KC-API-KEY": key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": pp,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
            "User-Agent": "DUP-Market-Radar/0.2.64-manual",
        }

    @staticmethod
    def _signed_client() -> httpx.Client:
        # No redirects: signed URL/headers must not follow off allowlisted origin.
        # Split connect vs read — hang on TCP must fail closed quickly.
        return httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            verify=True,
            follow_redirects=False,
        )

    @staticmethod
    def _assert_signed_path(path: str) -> str:
        """KuCoin REST path only — refuse traversal / off-API targets."""
        p = str(path or "")
        if not p.startswith("/api/") or ".." in p or "\\" in p or "://" in p:
            raise RuntimeError("kucoin signed path refused")
        if len(p) > 256:
            raise RuntimeError("kucoin signed path too long")
        return p

    def _signed_post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        path = self._assert_signed_path(path)
        base = self._creds["base_url"].rstrip("/")
        payload = json.dumps(body, separators=(",", ":"))
        headers = self._auth_headers("POST", path, payload)
        from radar.http_client import SIGNED_MAX_RESPONSE_BYTES, read_capped_json

        try:
            with self._signed_client() as client:
                resp = client.post(f"{base}{path}", content=payload, headers=headers)
                resp.raise_for_status()
                return read_capped_json(
                    resp, SIGNED_MAX_RESPONSE_BYTES, label="kucoin-signed"
                )
        except Exception as exc:  # noqa: BLE001
            # Drop cause chain so headers/sign never appear in traceback dumps
            raise RuntimeError(redact_exc(exc, max_len=256)) from None

    def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from radar.http_client import SIGNED_MAX_RESPONSE_BYTES, read_capped_json

        path = self._assert_signed_path(path)
        base = self._creds["base_url"].rstrip("/")
        if params:
            qs = urlencode({k: v for k, v in params.items() if v is not None})
            path_q = f"{path}?{qs}"
        else:
            path_q = path
        headers = self._auth_headers("GET", path_q, "")
        try:
            with self._signed_client() as client:
                resp = client.get(f"{base}{path_q}", headers=headers)
                resp.raise_for_status()
                return read_capped_json(
                    resp, SIGNED_MAX_RESPONSE_BYTES, label="kucoin-signed"
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(redact_exc(exc, max_len=256)) from None
