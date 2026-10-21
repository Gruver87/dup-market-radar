"""Telegram env allow-list parsing."""

from radar.telegram.env import telegram_allowed_chat_ids


def test_chat_ids_skip_non_int(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123,not-a-chat,456")
    with caplog.at_level("WARNING", logger="radar.telegram.env"):
        ids = telegram_allowed_chat_ids()
    assert ids == [123, 456]
    assert any("chat id refused" in r.message for r in caplog.records)


def test_chat_ids_soft_cap(monkeypatch, caplog):
    from radar.telegram import env as tg_env

    huge = ",".join(str(i) for i in range(1, tg_env._MAX_ALLOWED_CHATS + 20))
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", huge)
    with caplog.at_level("WARNING", logger="radar.telegram.env"):
        ids = telegram_allowed_chat_ids()
    assert len(ids) == tg_env._MAX_ALLOWED_CHATS
    assert any("truncated" in r.message for r in caplog.records)


def test_bot_token_length_gate(monkeypatch):
    from radar.telegram.env import telegram_bot_token

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "too-short")
    assert telegram_bot_token() is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 300)
    assert telegram_bot_token() is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
    assert telegram_bot_token() is not None
