"""Telegram API soft circuit — refuse spam when upstream is down."""

import pytest

from radar.telegram.client import TelegramClient, TelegramError


def test_circuit_opens_after_threshold():
    client = TelegramClient("fake-token-xxxxxxxx", fail_threshold=2, open_ms=60_000)
    client._circuit.record_fail("telegram", reason="a")
    client._circuit.record_fail("telegram", reason="b")
    assert client._circuit.allow("telegram") is False
    try:
        client.api("sendMessage", {"chat_id": 1, "text": "x"})
        assert False, "expected TelegramError"
    except TelegramError as exc:
        assert "circuit open" in str(exc).lower()


def test_telegram_method_charset_refused():
    client = TelegramClient("fake-token-xxxxxxxx")
    with pytest.raises(TelegramError, match="method"):
        client.api("../evil", {})
    with pytest.raises(TelegramError, match="method"):
        client.api("sendMessage?x=1", {})
    with pytest.raises(TelegramError, match="method"):
        client.api("send/Message", {})


def test_request_id_in_trade_event(tmp_path, monkeypatch):
    import radar.trade.events as ev
    from radar.request_ctx import request_id_var
    from radar.trade.events import record_trade_event

    path = tmp_path / "trade_events.jsonl"
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    token = request_id_var.set("rid-test-abc")
    try:
        record_trade_event(
            {"source": "manual", "side": "buy", "symbol": "ETHUSDT", "ok": True}
        )
    finally:
        request_id_var.reset(token)
    line = path.read_text(encoding="utf-8")
    assert "rid-test-abc" in line


def test_trade_event_message_truncated(tmp_path, monkeypatch):
    import json

    import radar.trade.events as ev
    from radar.trade.events import record_trade_event

    path = tmp_path / "trade_events.jsonl"
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    record_trade_event(
        {
            "source": "manual",
            "side": "buy",
            "symbol": "BTCUSDT",
            "ok": False,
            "message": "M" * 2000,
            "reason": "R" * 2000,
        }
    )
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert len(row["message"]) == 512
    assert len(row["reason"]) == 512
