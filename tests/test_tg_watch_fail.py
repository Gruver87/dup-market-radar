"""Post-buy auto-watch must not fail silently."""

from radar.telegram.bot import TelegramBot
from radar.telegram.stats import TG_STATS
from radar.telegram.watches import WatchBook


def test_auto_watch_after_buy_success(tmp_path):
    bot = TelegramBot(lambda: {})
    bot._book = WatchBook(tmp_path / "w.json")
    note = bot._auto_watch_after_buy(7, "BTCUSDT", 100_000_000)
    assert "spot added" in note
    assert bot._book.list_for_chat(7)[0]["symbol"] == "BTCUSDT"


def test_auto_watch_after_buy_surfaces_failure(tmp_path, monkeypatch):
    before = TG_STATS.snapshot()["watch_add_fail"]
    bot = TelegramBot(lambda: {})
    bot._book = WatchBook(tmp_path / "w.json")

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(bot._book, "add", boom)
    note = bot._auto_watch_after_buy(7, "ETHUSDT", None)
    assert "watch NOT added" in note
    assert "disk full" in note
    assert TG_STATS.snapshot()["watch_add_fail"] == before + 1


def test_cmd_trade_includes_watch_fail_in_reply(tmp_path, monkeypatch):
    bot = TelegramBot(lambda: {"ok": True, "sectors": {}})
    bot._book = WatchBook(tmp_path / "w.json")
    sent: list[str] = []
    monkeypatch.setattr(bot, "_send", lambda _c, t: sent.append(t))
    monkeypatch.setattr(
        bot,
        "_trade_place_fn",
        lambda **kwargs: {
            "ok": True,
            "dry_run": False,
            "message": "filled",
        },
    )
    monkeypatch.setattr(
        bot,
        "_auto_watch_after_buy",
        lambda *_a, **_k: "\n⚠ watch NOT added: boom",
    )
    bot._cmd_trade(1, "buy", ["AAAUSDT", "10", "confirm"])
    assert sent
    assert "watch NOT added" in sent[0]
