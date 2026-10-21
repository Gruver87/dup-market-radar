"""sanitize_exchange_surface / sanitize_trade_request — public trade soft caps."""

from radar.trade.kucoin_manual import sanitize_exchange_surface, sanitize_trade_request


def test_sanitize_exchange_keeps_code_msg_order_id():
    raw = {
        "code": "200000",
        "msg": "OK",
        "data": {"orderId": "68f0abcd1234567890abcdef", "extraHuge": "x" * 5000},
        "secretLeak": "should-drop",
    }
    out = sanitize_exchange_surface(raw)
    assert out == {
        "code": "200000",
        "msg": "OK",
        "data": {"orderId": "68f0abcd1234567890abcdef"},
    }
    assert "secretLeak" not in out
    assert "extraHuge" not in (out.get("data") or {})


def test_sanitize_exchange_none_and_non_dict():
    assert sanitize_exchange_surface(None) is None
    assert sanitize_exchange_surface("nope") is None  # type: ignore[arg-type]


def test_sanitize_exchange_caps_msg():
    out = sanitize_exchange_surface({"code": "400100", "msg": "m" * 1000})
    assert out is not None
    assert len(out["msg"]) == 256
    assert out["code"] == "400100"
    assert "data" not in out


def test_config_error_uses_basename_not_full_path(tmp_path):
    from radar.config import ConfigError, load_config

    missing = tmp_path / "nested" / "deep" / "missing.yaml"
    try:
        load_config(missing)
        raise AssertionError("expected ConfigError")
    except ConfigError as exc:
        msg = str(exc)
        assert "missing.yaml" in msg
        assert "nested" not in msg
        assert "deep" not in msg


def test_sanitize_trade_request_allowlist():
    raw = {
        "venue": "kucoin",
        "market": "spot",
        "order_type": "market",
        "symbol": "BTC-USDT",
        "side": "buy",
        "integer_money": True,
        "client_oid": "oid-" + ("x" * 100),
        "body": {
            "clientOid": "c1",
            "side": "buy",
            "symbol": "BTC-USDT",
            "type": "market",
            "funds": "10",
            "extraLeak": "drop-me",
        },
        "secret_blob": {"x": 1},
    }
    out = sanitize_trade_request(raw)
    assert out["body"].get("funds") == "10"
    assert "extraLeak" not in out["body"]
    assert "secret_blob" not in out
    assert len(out["client_oid"]) == 64
