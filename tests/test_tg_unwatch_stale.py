"""Unwatch on sell + skip alerts on stale snapshot."""

import time

from radar.telegram.bot import TelegramBot
from radar.telegram.stats import TG_STATS
from radar.telegram.watches import WatchBook


def test_unwatch_spot_removes_all_chats(tmp_path, monkeypatch):
    book_path = tmp_path / "tg.json"
    bot = TelegramBot(lambda: {})
    bot._allowed = [11, 22]
    bot._book = WatchBook(book_path)
    bot._book.ensure(11, "AAAUSDT", market="spot")
    bot._book.ensure(22, "AAAUSDT", market="spot")
    bot._book.ensure(11, "AAAUSDT", market="futures_long")
    n = bot.unwatch_spot("AAAUSDT")
    assert n == 2
    assert bot._book.list_for_chat(11)[0]["market"] == "futures_long"
    assert bot._book.list_for_chat(22) == []


def test_run_alerts_skips_stale_snapshot(tmp_path, monkeypatch):
    before = TG_STATS.snapshot()["alerts_skipped_stale"]
    bot = TelegramBot(
        lambda: {
            "generated_at_ms": int(time.time() * 1000) - 999_999,
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "ZZZUSDT",
                        "setup": "fade-watch",
                        "price_i": 100_000_000,
                        "score_centi": 6000,
                        "risk_flags": [],
                    }
                ]
            },
        }
    )
    bot._allowed = [1]
    bot._book = WatchBook(tmp_path / "w.json")
    bot._book.ensure(1, "ZZZUSDT", market="spot", entry_price_i=200_000_000)
    sent = []
    bot._client = type(
        "C",
        (),
        {"circuit_status": lambda self=None: {"open_remaining_ms": {}}},
    )()
    monkeypatch.setattr(bot, "_send", lambda chat, text: sent.append(text))
    monkeypatch.setattr(
        "radar.telegram.bot.load_config",
        lambda: {
            "telegram": {
                "snapshot_max_age_ms": 60_000,
                "stop_loss_bps": 100,
                "take_profit_warn_bps": 800,
                "alert_cooldown_ms": 0,
            },
            "auto_trade": {},
        },
    )
    bot._run_alerts()
    assert sent == []  # skipped due to stale snap
    assert TG_STATS.snapshot()["alerts_skipped_stale"] == before + 1
    assert bot.status()["stats"]["alerts_skipped_stale"] == before + 1

def test_run_alerts_skips_degraded_snapshot(tmp_path, monkeypatch):
    before = TG_STATS.snapshot().get("alerts_skipped_degraded", 0)
    bot = TelegramBot(
        lambda: {
            "ok": False,
            "generated_at_ms": int(time.time() * 1000),
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "ZZZUSDT",
                        "setup": "fade-watch",
                        "price_i": 100_000_000,
                        "score_centi": 6000,
                        "risk_flags": [],
                    }
                ]
            },
        }
    )
    bot._allowed = [1]
    bot._book = WatchBook(tmp_path / "w.json")
    bot._book.ensure(1, "ZZZUSDT", market="spot", entry_price_i=200_000_000)
    sent = []
    bot._client = type(
        "C",
        (),
        {"circuit_status": lambda self=None: {"open_remaining_ms": {}}},
    )()
    monkeypatch.setattr(bot, "_send", lambda chat, text: sent.append(text))
    monkeypatch.setattr(
        "radar.telegram.bot.load_config",
        lambda: {
            "telegram": {
                "snapshot_max_age_ms": 180_000,
                "stop_loss_bps": 100,
                "take_profit_warn_bps": 800,
                "alert_cooldown_ms": 0,
            },
            "auto_trade": {},
        },
    )
    bot._run_alerts()
    assert sent == []
    assert TG_STATS.snapshot().get("alerts_skipped_degraded", 0) == before + 1
