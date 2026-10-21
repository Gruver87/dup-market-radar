from radar.classify.sectors import base_asset, is_meme_symbol, is_stable_symbol, is_usdt_pair


def test_base_and_flags():
    assert base_asset("btc/usdt") == "BTC"
    assert is_usdt_pair("ETHUSDT")
    assert is_stable_symbol("USDCUSDT", ["USDC", "USDT"])
    assert is_meme_symbol("1000PEPEUSDT", ["PEPE"])
