"""i18n + world clock + FX (integer micros)."""

import pytest
from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.i18n import normalize_lang, t
from radar.ops.fx import FxError, convert, parse_amount_to_micros, validate_ccy
from radar.ops.world_time import world_clock
from radar.prefs import PrefsStore


def test_normalize_lang():
    assert normalize_lang("EN") == "en"
    assert normalize_lang("русский") == "ru"
    assert normalize_lang("nope") == "ru"


def test_t_help_both_langs():
    assert "/fx" in t("help", "ru")
    assert "/fx" in t("help", "en")
    assert "World clock" in t("help", "en") or "world" in t("help", "en").lower()


def test_t_format_args_truncated():
    out = t("fx_fail", "en", err="E" * 500)
    assert "E" * 256 in out
    assert "E" * 300 not in out


def test_world_clock_integer_epoch():
    data = world_clock(lang="en")
    assert data["ok"] is True
    assert isinstance(data["utc_epoch_ms"], int)
    assert len(data["cities"]) >= 5
    assert all("hhmmss" in c for c in data["cities"])


def test_parse_amount_micros():
    assert parse_amount_to_micros("100") == 100_000_000
    assert parse_amount_to_micros("1.5") == 1_500_000
    with pytest.raises(FxError):
        parse_amount_to_micros("-1")
    with pytest.raises(FxError):
        validate_ccy("USDT")


def test_fx_identity():
    out = convert(1_000_000, "USD", "USD")
    assert out["amount_out_micros"] == 1_000_000
    assert out["rate_micros"] == 1_000_000


def test_fx_convert_mocked(monkeypatch):
    import radar.ops.fx as fx

    monkeypatch.setattr(
        fx,
        "get_rates",
        lambda base="USD": {"USD": 1_000_000, "EUR": 920_000},
    )
    out = convert(100_000_000, "USD", "EUR")  # 100 USD
    assert out["ok"] is True
    assert out["amount_out_micros"] == 92_000_000  # 92 EUR micros


def test_prefs_lang(tmp_path):
    store = PrefsStore(tmp_path / "prefs.json")
    assert store.lang_for(42) == "ru"
    assert store.set_lang(42, "en") == "en"
    store2 = PrefsStore(tmp_path / "prefs.json")
    assert store2.lang_for(42) == "en"


def test_api_worldtime():
    client = TestClient(app_mod.app)
    res = client.get("/api/ops/worldtime?lang=en")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_api_fx_identity():
    client = TestClient(app_mod.app)
    res = client.get("/api/ops/fx?amount=10&from_ccy=USD&to_ccy=USD")
    assert res.status_code == 200
    body = res.json()
    assert body["amount_out_micros"] == body["amount_in_micros"]
    assert "circuit" in body


def test_api_fx_amount_too_long():
    client = TestClient(app_mod.app)
    long_amt = "1" * 25
    res = client.get(f"/api/ops/fx?amount={long_amt}&from_ccy=USD&to_ccy=EUR")
    assert res.status_code == 400
    assert "too long" in str(res.json().get("detail") or "").lower()


def test_fx_circuit_serves_stale(monkeypatch):
    import radar.ops.fx as fx
    from radar.ingest.circuit import VenueCircuitBreaker

    monkeypatch.setattr(fx, "_circuit", VenueCircuitBreaker(fail_threshold=1, open_ms=60_000))
    # Expired TTL so path hits circuit (not fresh-cache short-circuit)
    monkeypatch.setattr(
        fx,
        "_cache",
        {"USD": (0, {"USD": 1_000_000, "EUR": 900_000})},
    )
    fx._circuit.record_fail("frankfurter", reason="lab")
    assert fx._circuit.allow("frankfurter") is False

    def _boom(_base: str = "USD"):
        raise fx.FxError("upstream down")

    monkeypatch.setattr(fx, "_fetch_rates", _boom)
    rates = fx.get_rates("USD")
    assert rates["EUR"] == 900_000
    with pytest.raises(fx.FxError, match="circuit open"):
        monkeypatch.setattr(fx, "_cache", {})
        fx.get_rates("USD")
