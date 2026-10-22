"""MEXC / Bybit bootstrap fire independent GETs concurrently."""

from radar.ingest.bybit import _bootstrap_bybit
from radar.ingest.mexc import _bootstrap_mexc


class _Http:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url, *, params=None, use_cache=True):
        self.urls.append(url if not params else f"{url}?{params}")
        if "contract.mexc" in url:
            return {"data": [{"symbol": "BTC_USDT", "lastPrice": "1"}]}
        if "mexc.com/api/v3/exchangeInfo" in url:
            return {"symbols": [{"symbol": "BTCUSDT", "status": "1", "baseAsset": "BTC", "quoteAsset": "USDT"}]}
        if "mexc.com/api/v3/ticker" in url:
            return [{"symbol": "BTCUSDT", "lastPrice": "1", "quoteVolume": "9"}]
        if "category" in str(params) and params.get("category") == "spot" and "tickers" in url:
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "1"}]}}
        if params and params.get("category") == "linear":
            return {"result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "1"}]}}
        if "instruments-info" in url:
            return {"result": {"list": [{"symbol": "BTCUSDT", "launchTime": "1", "baseCoin": "BTC", "quoteCoin": "USDT"}]}}
        return {}


def test_mexc_bootstrap_three_endpoints():
    http = _Http()
    spot, fut, info = _bootstrap_mexc(http)
    assert isinstance(spot, list)
    assert isinstance(fut, dict)
    assert isinstance(info, dict)
    assert len(http.urls) == 3


def test_bybit_bootstrap_three_endpoints():
    http = _Http()
    spot, linear, instruments = _bootstrap_bybit(http)
    assert (spot.get("result") or {}).get("list")
    assert (linear.get("result") or {}).get("list")
    assert (instruments.get("result") or {}).get("list")
    assert len(http.urls) == 3
