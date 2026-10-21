"""Secrets must never appear in logs / audit / exception text."""

from __future__ import annotations

import io
import logging

from radar.audit_log import append_jsonl
from radar.pipeline import empty_snapshot
from radar.secrets_redact import (
    MAX_REDACT_CHARS,
    MAX_REGISTERED_SECRETS,
    REDACTED,
    RedactingFormatter,
    configure_secure_logging,
    redact_mapping,
    redact_text,
    register_secret,
)
import radar.secrets_redact as secrets_mod
from radar.telegram.client import TelegramClient, TelegramError


TG_TOKEN = "7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
KUCOIN_KEY = "kc-api-key-ABCDEF1234567890"
KUCOIN_SECRET = "kc-secret-XYZ9876543210abcd"
RADAR_KEY = "radar-gate-key-supersecret99"


def test_redact_telegram_bot_url_and_token():
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    out = redact_text(f"http failed: {url}")
    assert TG_TOKEN not in out
    assert "api.telegram.org/bot" in out
    assert REDACTED in out


def test_redact_registered_env_style_values(monkeypatch):
    monkeypatch.setenv("KUCOIN_API_KEY", KUCOIN_KEY)
    monkeypatch.setenv("KUCOIN_API_SECRET", KUCOIN_SECRET)
    monkeypatch.setenv("RADAR_API_KEY", RADAR_KEY)
    register_secret(TG_TOKEN)
    blob = (
        f"key={KUCOIN_KEY} secret={KUCOIN_SECRET} "
        f"radar={RADAR_KEY} tg={TG_TOKEN}"
    )
    out = redact_text(blob)
    assert KUCOIN_KEY not in out
    assert KUCOIN_SECRET not in out
    assert RADAR_KEY not in out
    assert TG_TOKEN not in out


def test_redact_mapping_sensitive_keys():
    row = redact_mapping(
        {
            "symbol": "BTCUSDT",
            "api_key": KUCOIN_KEY,
            "message": f"boom {TG_TOKEN}",
            "nested": {"token": TG_TOKEN, "ok": True},
        }
    )
    assert row["api_key"] == REDACTED
    assert row["nested"]["token"] == REDACTED
    assert TG_TOKEN not in row["message"]
    assert row["symbol"] == "BTCUSDT"


def test_audit_jsonl_scrubs_secrets(tmp_path):
    register_secret(TG_TOKEN)
    path = tmp_path / "audit.jsonl"
    append_jsonl(
        path,
        {"message": f"fail url=https://api.telegram.org/bot{TG_TOKEN}/x", "api_secret": "x"},
        max_bytes=1_000_000,
        keep=1,
    )
    text = path.read_text(encoding="utf-8")
    assert TG_TOKEN not in text
    assert '"api_secret": "***REDACTED***"' in text or REDACTED in text


def test_logging_formatter_scrubs_exception_text():
    configure_secure_logging()
    register_secret(TG_TOKEN)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    log = logging.getLogger("radar.test.secrets")
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.ERROR)
    log.propagate = False
    try:
        raise RuntimeError(
            f"Request URL is https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
        )
    except RuntimeError:
        log.exception("telegram poll failed")
    dumped = buf.getvalue()
    assert TG_TOKEN not in dumped
    assert REDACTED in dumped


def test_telegram_client_repr_hides_token():
    client = TelegramClient(TG_TOKEN)
    assert TG_TOKEN not in repr(client)
    assert "REDACTED" in repr(client)


def test_telegram_client_api_error_scrubs_token(monkeypatch):
    client = TelegramClient(TG_TOKEN)

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            raise RuntimeError(f"connect to {url}")

    monkeypatch.setattr("radar.telegram.client.httpx.Client", lambda **kw: Boom())
    try:
        client.api("getUpdates", {})
        assert False, "expected TelegramError"
    except TelegramError as exc:
        msg = str(exc)
        assert TG_TOKEN not in msg
        assert REDACTED in msg


def test_empty_snapshot_scrubs_reason():
    snap = empty_snapshot(
        f"scan failed: https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    )
    assert snap["ok"] is False
    joined = " ".join(str(e) for e in snap["errors"])
    assert TG_TOKEN not in joined
    assert REDACTED in joined


def test_fastapi_app_version_matches_package():
    from radar.api.app import app

    assert app.version == "0.2.62"


def test_redact_text_truncates_huge_input():
    huge = "a" * (MAX_REDACT_CHARS + 1000)
    out = redact_text(huge)
    assert len(out) <= MAX_REDACT_CHARS + len("...[redact_truncated]")
    assert out.endswith("...[redact_truncated]")


def test_redact_exc_truncates():
    from radar.secrets_redact import redact_exc

    out = redact_exc(RuntimeError("e" * 2000), max_len=64)
    assert len(out) <= 64 + len("...[redact_truncated]")
    assert out.endswith("...[redact_truncated]")


def test_register_secret_soft_cap(monkeypatch):
    with secrets_mod._extra_lock:
        secrets_mod._extra_secrets.clear()
    for i in range(MAX_REGISTERED_SECRETS + 10):
        register_secret(f"cap-secret-value-{i:04d}-xxxxxxxx")
    with secrets_mod._extra_lock:
        assert len(secrets_mod._extra_secrets) == MAX_REGISTERED_SECRETS
    # Beyond-cap value must not scrub
    overflow = f"cap-secret-value-{MAX_REGISTERED_SECRETS:04d}-xxxxxxxx"
    assert overflow in redact_text(f"leak={overflow}")
    # Cap-resident value still scrubs
    first = "cap-secret-value-0000-xxxxxxxx"
    assert REDACTED in redact_text(f"leak={first}")
    assert first not in redact_text(f"leak={first}")
    with secrets_mod._extra_lock:
        secrets_mod._extra_secrets.clear()
