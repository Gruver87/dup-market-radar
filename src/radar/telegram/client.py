"""Minimal Telegram Bot API client — TLS verify on, httpx only + soft circuit."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from radar.http_client import read_capped_json
from radar.ingest.circuit import VenueCircuitBreaker
from radar.secrets_redact import redact_exc, redact_text, register_secret

logger = logging.getLogger("radar.telegram.client")

# Bot API method names only — refuse path injection into token URL.
_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
TELEGRAM_MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB — getUpdates batches stay well under
TELEGRAM_API_HOST = "api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_sec: float = 35.0,
        fail_threshold: int = 3,
        open_ms: int = 60_000,
    ) -> None:
        if not token:
            raise TelegramError("empty bot token")
        self._token = token
        register_secret(token)
        # Bounded connect so a hung DNS/TCP peer cannot stall the bot loop.
        self._timeout = httpx.Timeout(float(timeout_sec), connect=5.0)
        self._circuit = VenueCircuitBreaker(
            fail_threshold=fail_threshold, open_ms=open_ms
        )

    def __repr__(self) -> str:
        return "TelegramClient(token=***REDACTED***)"

    def _method_url(self, method: str) -> str:
        # Token lives only in the request URL (Telegram Bot API constraint).
        # Never log this URL; exceptions are scrubbed below.
        name = str(method or "").strip()
        if not _METHOD_RE.fullmatch(name):
            raise TelegramError("telegram method charset refused")
        return f"https://{TELEGRAM_API_HOST}/bot{self._token}/{name}"

    def circuit_status(self) -> Dict[str, Any]:
        return self._circuit.status()

    def api(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._circuit.allow("telegram"):
            raise TelegramError("telegram circuit open — soft refuse")
        try:
            url = self._method_url(method)
        except TelegramError:
            raise
        try:
            with httpx.Client(
                timeout=self._timeout,
                verify=True,
                follow_redirects=False,
            ) as client:
                resp = client.post(url, json=payload or {})
                resp.raise_for_status()
                data = read_capped_json(
                    resp, TELEGRAM_MAX_RESPONSE_BYTES, label="telegram"
                )
        except TelegramError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe = redact_exc(exc, max_len=256)
            self._circuit.record_fail("telegram", reason=safe[:120])
            # from None — do not chain httpx cause (URL embeds bot token)
            raise TelegramError(f"http failed: {safe}") from None
        if not isinstance(data, dict):
            self._circuit.record_fail("telegram", reason="non-object json")
            raise TelegramError("telegram response not an object")
        if not data.get("ok"):
            desc = redact_text(str(data.get("description") or data))[:256]
            self._circuit.record_fail("telegram", reason=desc[:120])
            raise TelegramError(desc)
        self._circuit.record_ok("telegram")
        return data

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = "HTML",
        disable_preview: bool = True,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "text": text[:4000],
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            body["parse_mode"] = parse_mode
        return self.api("sendMessage", body)

    def get_updates(
        self,
        *,
        offset: Optional[int] = None,
        timeout: int = 25,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        # Telegram allows 1..100; keep batches small for handler latency.
        lim = max(1, min(100, int(limit)))
        payload: Dict[str, Any] = {
            "timeout": int(timeout),
            "limit": lim,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = int(offset)
        data = self.api("getUpdates", payload)
        result = data.get("result")
        if not isinstance(result, list):
            return []
        return result[:lim]
