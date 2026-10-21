"""Nearest analysis entry/exit + alert evaluation (no Telegram network)."""

from radar.money import PRICE_SCALE
from radar.telegram.alerts import evaluate_watch, format_alert
from radar.telegram.nearest import nearest_for_action
from radar.telegram.watches import WatchBook


def test_nearest_buy_pullback_zone():
    mark = 100 * PRICE_SCALE
    n = nearest_for_action(action="buy", price_i=mark, setup="buy-watch")
    assert n["ok"] is True
    assert n["basis"] == "analysis_zone"
    assert n["zone_lo_i"] < mark
    assert n["nearest_price_i"] == n["zone_lo_i"]  # buy-watch → prefer lo
    assert "disclaimer" in n


def test_nearest_sell_exit_zone():
    mark = 100 * PRICE_SCALE
    n = nearest_for_action(action="sell", price_i=mark, setup="fade-watch")
    assert n["ok"] is True
    assert n["zone_hi_i"] > mark
    assert n["nearest_price_i"] >= mark


def test_alert_spot_fade_warns_sell():
    snap = {
        "sectors": {
            "spot_alts": [
                {
                    "symbol": "AAAUSDT",
                    "setup": "fade-watch",
                    "price_i": 90 * PRICE_SCALE,
                    "score_centi": 6000,
                    "risk_flags": [],
                }
            ]
        }
    }
    watch = {
        "chat_id": 1,
        "symbol": "AAAUSDT",
        "market": "spot",
        "entry_price_i": 100 * PRICE_SCALE,
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    alert = evaluate_watch(watch, snap, stop_loss_bps=500, take_profit_warn_bps=800)
    assert alert is not None
    assert alert["action_suggest"] == "sell"
    assert alert["severity"] in {"warn", "critical"}
    text = format_alert(alert)
    assert "AAAUSDT" in text
    assert "nearest" in text.lower() or "ближайш" in text.lower()


def test_alert_futures_long_flips_to_short():
    snap = {
        "sectors": {
            "futures_long": [],
            "futures_short": [
                {
                    "symbol": "BTCUSDT",
                    "setup": "short",
                    "price_i": 50_000 * PRICE_SCALE,
                    "score_centi": 7000,
                    "risk_flags": [],
                }
            ],
        }
    }
    watch = {
        "chat_id": 42,
        "symbol": "BTCUSDT",
        "market": "futures_long",
        "entry_price_i": 48_000 * PRICE_SCALE,
        "last_alert_at_ms": 0,
        "last_alert_key": "",
    }
    alert = evaluate_watch(watch, snap)
    assert alert is not None
    assert alert["action_suggest"] == "close_long"
    assert alert["severity"] == "critical"


def test_watch_book_persist(tmp_path):
    book = WatchBook(tmp_path / "tg_watches.json")
    book.add(7, "eth-usdt", market="spot", entry_price_i=100_000_000)
    book.add(7, "BTCUSDT", market="futures_long", entry_price_i=200_000_000)
    assert len(book.list_for_chat(7)) == 2
    book2 = WatchBook(tmp_path / "tg_watches.json")
    assert len(book2.list_for_chat(7)) == 2
    assert book2.remove(7, "ETHUSDT") == 1
