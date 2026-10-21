import pytest

from radar.config import ConfigError, load_config, validate_config, venue_priority


def test_default_config_validates():
    cfg = load_config()
    assert cfg["venues"][0] == "kucoin"
    assert venue_priority(cfg)["kucoin"] == 0


def test_rejects_futures_in_auto_sectors():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["auto_trade"] = dict(cfg["auto_trade"])
    cfg["auto_trade"]["allowed_sectors"] = ["spot_alts", "futures"]
    with pytest.raises(ConfigError, match="futures"):
        validate_config(cfg)


def test_rejects_missing_kucoin():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["venues"] = ["binance", "bybit"]
    with pytest.raises(ConfigError, match="kucoin"):
        validate_config(cfg)


def test_rejects_bad_top_n():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["top_n"] = 0
    with pytest.raises(ConfigError, match="top_n"):
        validate_config(cfg)


def test_rejects_cors_star():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["api"] = dict(cfg.get("api") or {})
    cfg["api"]["cors_origins"] = ["http://127.0.0.1:8787", "*"]
    with pytest.raises(ConfigError, match="cors"):
        validate_config(cfg)


def test_rejects_trusted_hosts_star():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["api"] = dict(cfg.get("api") or {})
    cfg["api"]["trusted_hosts"] = ["localhost", "*"]
    with pytest.raises(ConfigError, match="trusted_hosts"):
        validate_config(cfg)


def test_rejects_auto_buy_above_order_cap():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["api"] = dict(cfg.get("api") or {})
    cfg["api"]["max_order_quote_usdt_micros"] = 5_000_000
    cfg["auto_trade"] = dict(cfg["auto_trade"])
    cfg["auto_trade"]["quote_usdt_micros_per_buy"] = 10_000_000
    with pytest.raises(ConfigError, match="quote_usdt_micros_per_buy"):
        validate_config(cfg)


def test_rejects_allowlist_hosts_wildcard():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["allowlist_hosts"] = ["api.kucoin.com", "*"]
    with pytest.raises(ConfigError, match="allowlist_hosts"):
        validate_config(cfg)


def test_rejects_allowlist_hosts_url_or_path():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["allowlist_hosts"] = ["https://api.kucoin.com"]
    with pytest.raises(ConfigError, match="allowlist_hosts"):
        validate_config(cfg)
    cfg["allowlist_hosts"] = ["api.kucoin.com/path"]
    with pytest.raises(ConfigError, match="allowlist_hosts"):
        validate_config(cfg)


def test_rejects_allowlist_hosts_dot_entries():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["allowlist_hosts"] = ["."]
    with pytest.raises(ConfigError, match="allowlist_hosts"):
        validate_config(cfg)


def test_rejects_allowlist_hosts_too_many():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["allowlist_hosts"] = [f"h{i}.example.com" for i in range(65)]
    with pytest.raises(ConfigError, match="allowlist_hosts"):
        validate_config(cfg)


def test_rejects_cors_origins_too_many():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["api"] = dict(cfg.get("api") or {})
    cfg["api"]["cors_origins"] = [f"http://127.0.0.1:{8000 + i}" for i in range(33)]
    with pytest.raises(ConfigError, match="cors_origins"):
        validate_config(cfg)


def test_rejects_venues_too_many():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["venues"] = ["kucoin"] + [f"x{i}" for i in range(8)]
    with pytest.raises(ConfigError, match="venues"):
        validate_config(cfg)


def test_rejects_bad_poll_sec():
    cfg = load_config()
    cfg = dict(cfg)
    sectors = dict(cfg["sectors"])
    sectors["memes"] = dict(sectors["memes"])
    sectors["memes"]["poll_sec"] = 2
    cfg["sectors"] = sectors
    with pytest.raises(ConfigError, match="poll_sec"):
        validate_config(cfg)
    sectors["memes"]["poll_sec"] = 4000
    with pytest.raises(ConfigError, match="poll_sec"):
        validate_config(cfg)
    sectors["memes"]["poll_sec"] = "30"
    with pytest.raises(ConfigError, match="poll_sec"):
        validate_config(cfg)
