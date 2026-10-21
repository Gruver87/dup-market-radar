"""WatchBook soft capacity refuse."""

import pytest

from radar.telegram.watches import WatchBook, WatchBookFull


def test_watch_book_soft_full(tmp_path):
    book = WatchBook(tmp_path / "watches.json", max_watches=2)
    book.add(1, "BTCUSDT", market="spot")
    book.add(1, "ETHUSDT", market="spot")
    with pytest.raises(WatchBookFull):
        book.add(1, "SOLUSDT", market="spot")
    # Update existing key still OK
    book.add(1, "BTCUSDT", market="spot", entry_price_i=100)
    assert book.status()["count"] == 2
    assert book.status()["max_watches"] == 2


def test_ensure_refuses_when_full(tmp_path):
    book = WatchBook(tmp_path / "watches.json", max_watches=1)
    assert book.ensure(1, "BTCUSDT") is True
    with pytest.raises(WatchBookFull):
        book.ensure(1, "ETHUSDT")
