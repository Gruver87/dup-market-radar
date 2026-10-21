from radar.ingest.kucoin import normalize_symbol


def test_normalize_spot_and_futures():
    assert normalize_symbol("BTC-USDT") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT") == "ETHUSDT"
    assert normalize_symbol("XBTUSDTM", futures=True) == "BTCUSDT"
    assert normalize_symbol("ETHUSDTM", futures=True) == "ETHUSDT"
