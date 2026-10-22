"""Binance bootstrap fires spot/fut/premium/exchangeInfo concurrently."""

from radar.ingest.binance import _bootstrap_binance


class _FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url, *, params=None, use_cache=True):
        self.urls.append(url)
        if "exchangeInfo" in url:
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]}
        if "premiumIndex" in url:
            return [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}]
        if "fapi" in url:
            return [{"symbol": "BTCUSDT", "lastPrice": "1", "quoteVolume": "9"}]
        return [{"symbol": "BTCUSDT", "lastPrice": "1", "quoteVolume": "9"}]


def test_bootstrap_binance_hits_four_endpoints():
    http = _FakeHttp()
    spot, fut, prem, info = _bootstrap_binance(http)
    assert isinstance(spot, list)
    assert isinstance(fut, list)
    assert isinstance(prem, list)
    assert isinstance(info, dict)
    joined = " ".join(http.urls)
    assert "api/v3/ticker/24hr" in joined
    assert "fapi/v1/ticker/24hr" in joined
    assert "premiumIndex" in joined
    assert "exchangeInfo" in joined
    assert len(http.urls) == 4
