"""Discord-compatible webhook mirror for alerts / trades.

Fail-soft: never raises to callers. URL from env only (no secrets in YAML).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from radar.secrets_redact import redact_exc, redact_text

logger = logging.getLogger("radar.notify.webhook")

_ALLOWED_HOSTS = frozenset(
    {
        "discord.com",
        "discordapp.com",
        "hooks.slack.com",
    }
)


def _env_url() -> Optional[str]:
    for key in ("RADAR_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return None


def _host_ok(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if p.scheme != "https":
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host in _ALLOWED_HOSTS:
        return True
    # Discord CDN-style subdomains
    return host.endswith(".discord.com") or host.endswith(".discordapp.com")


class WebhookNotifier:
    def __init__(self, url: Optional[str] = None) -> None:
        self._url = url if url is not None else _env_url()
        self._lock = threading.Lock()
        self._sent = 0
        self._failed = 0
        self._refused = 0

    def configured(self) -> bool:
        return bool(self._url) and _host_ok(str(self._url))

    def status(self) -> Dict[str, Any]:
        ok = self.configured()
        return {
            "configured": ok,
            "sent": self._sent,
            "failed": self._failed,
            "refused": self._refused,
            "host_ok": _host_ok(str(self._url)) if self._url else False,
        }

    def send_text(self, content: str, *, kind: str = "event") -> bool:
        if not self._url:
            return False
        if not _host_ok(self._url):
            with self._lock:
                self._refused += 1
            logger.warning("webhook refused — host not allowlisted")
            return False
        text = redact_text(str(content or ""))[:1800]
        if not text.strip():
            return False
        # Discord content hard limit ~2000; prefix kind for ops clarity
        body = {"content": f"**Radar · {kind}**\n{text}"[:1900]}
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                r = client.post(self._url, json=body)
            if 200 <= r.status_code < 300:
                with self._lock:
                    self._sent += 1
                return True
            with self._lock:
                self._failed += 1
            logger.warning(
                "webhook HTTP %s kind=%s",
                r.status_code,
                redact_text(kind)[:32],
            )
            return False
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._failed += 1
            logger.warning("webhook send failed: %s", redact_exc(exc, max_len=128))
            return False

    def send_alert(self, alert: Dict[str, Any]) -> bool:
        sym = str(alert.get("symbol") or "?")
        sev = str(alert.get("severity") or "info").upper()
        setup = str(alert.get("setup") or "")
        action = str(alert.get("action_suggest") or "")
        reasons = "; ".join(str(r)[:80] for r in (alert.get("reasons") or [])[:6])
        return self.send_text(
            f"{sev} {sym} setup={setup} → {action}\n{reasons}",
            kind="alert",
        )

    def send_trade(self, event: Dict[str, Any]) -> bool:
        side = str(event.get("side") or "?")
        sym = str(event.get("symbol") or "?")
        ok = "ok" if event.get("ok") else "fail"
        dry = " dry_run" if event.get("dry_run") else ""
        msg = redact_text(str(event.get("message") or ""))[:200]
        return self.send_text(
            f"{ok}{dry} {side} {sym}\n{msg}",
            kind="trade",
        )


_NOTIFIER: Optional[WebhookNotifier] = None
_NOTIFIER_LOCK = threading.Lock()


def get_webhook_notifier() -> WebhookNotifier:
    global _NOTIFIER
    with _NOTIFIER_LOCK:
        if _NOTIFIER is None:
            _NOTIFIER = WebhookNotifier()
        return _NOTIFIER
