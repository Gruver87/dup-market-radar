"""ClientOid ledger — duplicate refuse + persist."""

import json

import pytest

from radar.trade.oid_ledger import ClientOidLedger
from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest


def test_claim_duplicate_refused(tmp_path):
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600, max_entries=100)
    assert led.claim("oid-aaaa-1111", side="buy", symbol="BTC-USDT") is True
    assert led.claim("oid-aaaa-1111", side="buy", symbol="BTC-USDT") is False
    assert led.seen("oid-aaaa-1111") is True


def test_release_allows_reclaim(tmp_path):
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    assert led.claim("oid-bbbb-2222", side="sell", symbol="ETH-USDT") is True
    led.release("oid-bbbb-2222")
    assert led.claim("oid-bbbb-2222", side="sell", symbol="ETH-USDT") is True


def test_persist_reload(tmp_path):
    path = tmp_path / "oid.json"
    a = ClientOidLedger(path, ttl_sec=3600)
    a.claim("oid-cccc-3333", side="buy", symbol="SOL-USDT")
    a.mark_accepted("oid-cccc-3333", order_id="ex-1")
    b = ClientOidLedger(path, ttl_sec=3600)
    assert b.seen("oid-cccc-3333") is True
    assert b.claim("oid-cccc-3333", side="buy", symbol="SOL-USDT") is False


def test_trader_duplicate_oid_soft_refuse(tmp_path, monkeypatch):
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(led, sell_clamp=False, enforce_symbol_meta=False)

    monkeypatch.setattr(
        "radar.trade.kucoin_manual.trading_configured", lambda: True
    )
    calls = {"n": 0}

    def fake_post(path, body):
        calls["n"] += 1
        return {"code": "200000", "data": {"orderId": f"ord-{calls['n']}"}}

    monkeypatch.setattr(trader, "_signed_post", fake_post)

    r1 = trader.place(
        TradeRequest(
            symbol="BTCUSDT",
            side="buy",
            confirm=True,
            quote_usdt_micros=1_000_000,
            client_oid="dup-oid-zzzzzzzz",
        )
    )
    assert r1.ok is True
    assert calls["n"] == 1

    r2 = trader.place(
        TradeRequest(
            symbol="BTCUSDT",
            side="buy",
            confirm=True,
            quote_usdt_micros=1_000_000,
            client_oid="dup-oid-zzzzzzzz",
        )
    )
    assert r2.ok is False
    assert r2.dry_run is True
    assert "duplicate" in r2.message.lower()
    assert calls["n"] == 1


def test_corrupt_ledger_refuses_claim(tmp_path):
    path = tmp_path / "oid.json"
    path.write_text("{not-json", encoding="utf-8")
    led = ClientOidLedger(path, ttl_sec=3600)
    st = led.status()
    assert st["persist_ok"] is False
    assert st["integrity"] == "corrupt"
    with pytest.raises(RuntimeError, match="persist degraded"):
        led.claim("oid-dddd-4444", side="buy", symbol="BTC-USDT")


def test_checksum_mismatch_refuses_claim(tmp_path):
    path = tmp_path / "oid.json"
    path.write_text(
        json.dumps({"entries": {}, "updated_at": 1.0}, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "oid.json.sha256").write_text("deadbeef\n", encoding="utf-8")
    led = ClientOidLedger(path, ttl_sec=3600)
    assert led.status()["persist_ok"] is False
    with pytest.raises(RuntimeError, match="persist degraded"):
        led.claim("oid-eeee-5555", side="buy", symbol="ETH-USDT")


def test_claim_stores_int_epoch_at(tmp_path):
    path = tmp_path / "oid.json"
    led = ClientOidLedger(path, ttl_sec=3600)
    assert led.claim("oid-ffff-6666", side="buy", symbol="BTC-USDT") is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    at = raw["entries"]["oid-ffff-6666"]["at"]
    assert isinstance(at, int)
    assert isinstance(raw["updated_at"], int)


def test_legacy_float_at_still_prunes(tmp_path):
    path = tmp_path / "oid.json"
    # Old ledger with float epoch seconds — still loadable / prunable
    led = ClientOidLedger(path, ttl_sec=3600)
    led.claim("oid-gggg-7777", side="buy", symbol="ETH-USDT")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"]["oid-gggg-7777"]["at"] = float(raw["entries"]["oid-gggg-7777"]["at"])
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    # Drop checksum so reload uses missing_checksum path (still loads data)
    csum = path.with_name(path.name + ".sha256")
    if csum.exists():
        csum.unlink()
    b = ClientOidLedger(path, ttl_sec=3600)
    assert b.seen("oid-gggg-7777") is True
