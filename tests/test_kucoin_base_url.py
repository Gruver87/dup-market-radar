"""KuCoin API base URL allowlist (SSRF fail-closed)."""

from radar.trade.env_load import trading_configured, validate_kucoin_base_url
from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest


def test_default_base_ok():
    url, err = validate_kucoin_base_url(None)
    assert err is None
    assert url == "https://api.kucoin.com"


def test_http_refused():
    url, err = validate_kucoin_base_url("http://api.kucoin.com")
    assert url == ""
    assert err and "https" in err


def test_unknown_host_refused():
    url, err = validate_kucoin_base_url("https://evil.example/steal")
    assert url == ""
    assert err and "refused" in err


def test_credentials_in_url_refused():
    url, err = validate_kucoin_base_url("https://user:pass@api.kucoin.com")
    assert url == ""
    assert err and "credential" in err


def test_path_stripped_to_origin():
    url, err = validate_kucoin_base_url("https://api.kucoin.com/api/v1/orders?x=1")
    assert err is None
    assert url == "https://api.kucoin.com"


def test_place_refuses_bad_base(monkeypatch, tmp_path):
    monkeypatch.setenv("KUCOIN_API_KEY", "k" * 16)
    monkeypatch.setenv("KUCOIN_API_SECRET", "s" * 16)
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "p" * 8)
    monkeypatch.setenv("KUCOIN_API_BASE", "https://evil.example")
    assert trading_configured() is False
    t = KuCoinManualTrader(enforce_symbol_meta=False)
    r = t.place(
        TradeRequest(
            symbol="BTCUSDT",
            side="buy",
            confirm=False,
            quote_usdt_micros=1_000_000,
        )
    )
    assert r.ok is False
    assert "base URL" in r.message or "refused" in r.message.lower()


def test_cred_length_gate(monkeypatch):
    from radar.trade.env_load import kucoin_creds

    monkeypatch.setenv("KUCOIN_API_KEY", "short")
    monkeypatch.setenv("KUCOIN_API_SECRET", "s" * 16)
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "p" * 8)
    monkeypatch.delenv("KUCOIN_API_BASE", raising=False)
    c = kucoin_creds()
    assert c["api_key"] == ""
    monkeypatch.setenv("KUCOIN_API_KEY", "k" * 300)
    c = kucoin_creds()
    assert c["api_key"] == ""
    assert trading_configured() is False
