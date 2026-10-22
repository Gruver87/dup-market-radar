"""KuCoin listings reuse contracts/active payload — no second GET."""

from radar.ingest.kucoin import _listings


class _CountingHttp:
    def __init__(self) -> None:
        self.hosts: list[str] = []

    def get_json(self, url, *, params=None, use_cache=True):
        self.hosts.append(url)
        if "symbols" in url:
            return {
                "data": [
                    {
                        "symbol": "AAA-USDT",
                        "enableTrading": True,
                        "tradingStartTime": 1_700_000_000_000,
                        "baseCurrency": "AAA",
                        "quoteCurrency": "USDT",
                    }
                ]
            }
        if "contracts/active" in url:
            raise AssertionError("contracts/active must not be re-fetched")
        raise AssertionError(f"unexpected url {url}")


def test_listings_reuses_contracts_data():
    http = _CountingHttp()
    contracts = [
        {
            "symbol": "XBTUSDTM",
            "status": "Open",
            "firstOpenDate": 1_700_000_100_000,
            "baseCurrency": "XBT",
            "quoteCurrency": "USDT",
        }
    ]
    rows = _listings(http, contracts_data=contracts)
    assert any(r.get("market") == "spot" and r.get("symbol") == "AAAUSDT" for r in rows)
    assert any(r.get("market") == "futures" and r.get("symbol") == "BTCUSDT" for r in rows)
    assert all("contracts/active" not in u for u in http.hosts)
    assert len(http.hosts) == 1
