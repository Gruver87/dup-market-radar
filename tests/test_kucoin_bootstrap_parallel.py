"""KuCoin bootstrap + whale fan-out."""

from radar.ingest.kucoin import _bootstrap_kucoin, _sample_whales


class _FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url, *, params=None, use_cache=True, **_kw):
        self.urls.append(url)
        if "allTickers" in url:
            return {"data": {"ticker": [{"symbol": "BTC-USDT", "last": "1"}]}}
        if "contracts/active" in url:
            return {"data": [{"symbol": "XBTUSDTM", "status": "Open"}]}
        if "symbols" in url:
            return {
                "data": [
                    {
                        "symbol": "BTC-USDT",
                        "enableTrading": True,
                        "tradingStartTime": 1,
                        "baseCurrency": "BTC",
                        "quoteCurrency": "USDT",
                    }
                ]
            }
        if "histories" in url:
            return {
                "data": [
                    {
                        "price": "1",
                        "size": "2",
                        "side": "buy",
                        "time": 1_700_000_000_000,
                    }
                ]
            }
        raise RuntimeError(f"unexpected {url}")


def test_bootstrap_kucoin_three_endpoints():
    """Tickers∥contracts then symbols — still three GETs, symbols last."""
    http = _FakeHttp()
    spot, contracts, symbols = _bootstrap_kucoin(http)
    assert (spot.get("data") or {}).get("ticker")
    assert isinstance(contracts.get("data"), list)
    assert isinstance(symbols.get("data"), list)
    assert len(http.urls) == 3
    assert "allTickers" in http.urls[0] or "allTickers" in http.urls[1]
    assert "symbols" in http.urls[-1]


def test_sample_whales_parallel_soft_ok():
    http = _FakeHttp()
    rows = _sample_whales(http, ["BTC-USDT", "ETH-USDT"])
    assert len(rows) >= 2
    assert all(r.get("venue") == "kucoin" for r in rows)


def test_fetch_kucoin_whales_after_all_tickers():
    """0.2.84: histories only after allTickers; may overlap /v2/symbols."""
    from radar.ingest.kucoin import fetch_kucoin

    http = _FakeHttp()
    pack = fetch_kucoin(http, whale_symbols=["BTCUSDT", "ETHUSDT"])
    assert pack.get("venue") == "kucoin"
    assert pack.get("spot")
    assert pack.get("whales")
    ticker_i = next(i for i, u in enumerate(http.urls) if "allTickers" in u)
    hist_is = [i for i, u in enumerate(http.urls) if "histories" in u]
    assert hist_is
    assert all(i > ticker_i for i in hist_is)
    # symbols is present and not required to precede histories (phase2 parallel)
    assert any("symbols" in u for u in http.urls)
