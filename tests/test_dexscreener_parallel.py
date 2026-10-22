"""DexScreener expand parallel + search soft-skip when boosted fills."""

from radar.ingest import dexscreener as ds


class _Http:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url, *, params=None, use_cache=True):
        self.urls.append(url)
        if "token-boosts" in url:
            return [
                {"chainId": "solana", "tokenAddress": f"tok{i}"} for i in range(12)
            ]
        if "/tokens/" in url:
            return {
                "pairs": [
                    {
                        "baseToken": {"symbol": "X"},
                        "quoteToken": {"symbol": "Y"},
                        "chainId": "solana",
                        "pairAddress": url[-8:],
                        "priceUsd": "1",
                        "priceChange": {"h24": "1", "h1": "1", "m5": "1"},
                        "liquidity": {"usd": "100000"},
                        "volume": {"h24": "1000"},
                    }
                ]
            }
        if "search" in url:
            raise AssertionError("search must be skipped when boosted fills")
        raise AssertionError(url)


def test_search_skipped_when_boosted_enough(monkeypatch):
    monkeypatch.setattr(ds, "_SEARCH_SKIP_MIN_ROWS", 5)
    http = _Http()
    rows = ds.fetch_meme_pairs(http, ["solana"])
    assert len(rows) >= 5
    assert not any("search" in u for u in http.urls)
    assert any("token-boosts" in u for u in http.urls)
