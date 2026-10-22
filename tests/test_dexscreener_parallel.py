"""DexScreener batched token expand + search soft-skip when boosted fills."""

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
            # Batched path: one URL with comma-separated addresses.
            assert "," in url.split("/tokens/")[-1] or url.endswith("tok0")
            n = url.count(",") + 1
            return {
                "pairs": [
                    {
                        "baseToken": {"symbol": f"X{i}"},
                        "quoteToken": {"symbol": "Y"},
                        "chainId": "solana",
                        "pairAddress": f"pair{i}",
                        "priceUsd": "1",
                        "priceChange": {"h24": "1", "h1": "1", "m5": "1"},
                        "liquidity": {"usd": "100000"},
                        "volume": {"h24": "1000"},
                    }
                    for i in range(min(n * 2, 24))
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
    token_urls = [u for u in http.urls if "/tokens/" in u]
    assert len(token_urls) == 1
    assert token_urls[0].count(",") == 11  # 12 addresses → 11 commas


def test_token_address_chunks_dedupe_and_cap():
    items = [{"tokenAddress": "a"}, {"tokenAddress": "a"}, {"tokenAddress": "b"}]
    chunks = ds._token_address_chunks(items, batch_size=30)
    assert chunks == [["a", "b"]]
