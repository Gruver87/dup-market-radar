"""Telegram per-chat command soft rate-limit."""

from radar.rate_limit import RateLimiter
from radar.telegram.bot import TelegramBot
from radar.telegram.stats import TG_STATS
from radar.telegram.watches import WatchBook


def test_cmd_rate_limit_soft_refuse(tmp_path, monkeypatch):
    bot = TelegramBot(lambda: {})
    bot._book = WatchBook(tmp_path / "w.json")
    bot._cmd_limiter = RateLimiter(max_per_window=1, window_ms=60_000)
    sent: list[str] = []
    monkeypatch.setattr(bot, "_send", lambda _c, t: sent.append(t))
    before = TG_STATS.snapshot()["cmd_rate_refused"]
    bot._handle(42, "/help")
    assert sent and "DUP Market Radar" in sent[0]
    sent.clear()
    bot._handle(42, "/status")
    assert sent
    assert "soft refuse" in sent[0].lower() or "слишком много" in sent[0].lower()
    assert TG_STATS.snapshot()["cmd_rate_refused"] == before + 1
