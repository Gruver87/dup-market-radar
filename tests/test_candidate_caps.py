"""Score candidate() string/list/metrics soft caps (0.2.40)."""

from radar.score.common import candidate
from radar.telegram.alerts import format_alert


def test_candidate_caps_flags_hint_url_chain():
    flags = [f"flag_{i}_" + ("x" * 80) for i in range(20)]
    out = candidate(
        sector="spot",
        row={
            "symbol": "BTCUSDT",
            "venue": "kucoin",
            "price_i": 100,
            "change_bps": 10,
            "quote_volume_i": 1_000_000,
            "url": "http://evil.example/" + ("u" * 300),
            "chain": "ethereum-mainnet-extra-long-name",
        },
        score_centi=5000,
        setup="breakout-" + ("s" * 100),
        entry_hint="hint-" + ("h" * 400),
        risk_flags=flags,
        metrics={"a": 1.5, "b": "ok", **{f"k{i}": i for i in range(40)}},
    )
    assert len(out["risk_flags"]) == 8
    assert all(len(f) <= 64 for f in out["risk_flags"])
    assert len(out["setup"]) == 64
    assert len(out["entry_hint"]) == 256
    assert out["url"] is None  # non-https refused
    assert len(out["chain"]) == 32
    assert "a" not in out["metrics"]  # float money refused
    assert out["metrics"]["b"] == "ok"
    assert len(out["metrics"]) <= 32


def test_candidate_keeps_https_url_capped():
    out = candidate(
        sector="memes",
        row={
            "symbol": "PEPEUSDT",
            "url": "https://example.com/" + ("p" * 300),
            "chain": "sol",
        },
        score_centi=100,
        setup="watch",
        entry_hint="near",
        risk_flags=["thin_book"],
        metrics={},
    )
    assert out["url"].startswith("https://")
    assert len(out["url"]) == 256


def test_format_alert_caps_reasons_count_and_length():
    reasons = [f"r{i}-" + ("z" * 400) for i in range(30)]
    text = format_alert(
        {
            "severity": "info",
            "symbol": "BTCUSDT",
            "market": "spot",
            "setup": "watch",
            "price_display": "1.0",
            "score_display": "10.00",
            "action_suggest": "hold",
            "reasons": reasons,
        }
    )
    # at most 16 reason lines (prefix "· ")
    reason_lines = [ln for ln in text.splitlines() if ln.startswith("· ")]
    assert len(reason_lines) == 16
    assert all(len(ln) <= 2 + 256 for ln in reason_lines)
