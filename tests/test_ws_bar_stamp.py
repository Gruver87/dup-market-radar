"""WS bar stamp + HTTP kline skip promotion."""

from __future__ import annotations

from radar.ingest.ws_bar_stamp import stamp_ws_bar_metrics


def test_promote_dense_15m_skips_http() -> None:
    uni = {"spot": [{"symbol": "BTCUSDT", "price_i": 1}]}
    bars = {
        "BTCUSDT": {
            "change_bps_ws_15m": 42,
            "ws_bar_15m_trade_count": 10,
            "change_bps_ws_1m": 5,
        }
    }
    out, n, skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert n == 1
    assert "BTCUSDT" in skip
    row = out["spot"][0]
    assert row["change_bps_15m"] == 42
    assert row["change_bps_15m_source"] == "native_ws_bar_15m"
    assert row["mtf_source"] == "native_ws"
    assert row.get("mtf_attached_at_ms")


def test_promote_dense_5m_proxy() -> None:
    uni = {"spot": [{"symbol": "ETHUSDT", "price_i": 1}]}
    bars = {
        "ETHUSDT": {
            "change_bps_ws_5m": 77,
            "ws_bar_5m_trade_count": 5,
        }
    }
    out, n, skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert n == 1
    assert "ETHUSDT" in skip
    assert out["spot"][0]["change_bps_15m"] == 77
    assert out["spot"][0]["change_bps_15m_source"] == "native_ws_bar_5m_proxy"


def test_no_promote_when_sparse() -> None:
    uni = {"spot": [{"symbol": "SOLUSDT", "price_i": 1}]}
    bars = {
        "SOLUSDT": {
            "change_bps_ws_15m": 9,
            "ws_bar_15m_trade_count": 1,  # below dense floor
        }
    }
    out, n, skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert n == 1
    assert not skip
    assert out["spot"][0].get("change_bps_15m") is None


def test_http_15m_untouched() -> None:
    uni = {"spot": [{"symbol": "BTCUSDT", "price_i": 1, "change_bps_15m": 100}]}
    bars = {
        "BTCUSDT": {
            "change_bps_ws_15m": 42,
            "ws_bar_15m_trade_count": 10,
        }
    }
    out, _n, skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert out["spot"][0]["change_bps_15m"] == 100
    assert not skip  # already had HTTP — no promote/skip


def test_dense_1h_soft_fills_change_bps_1h() -> None:
    uni = {
        "spot": [
            {
                "symbol": "BTCUSDT",
                "price_i": 1,
                "change_bps_15m": 42,
                "mtf_source": "native_ws",
            }
        ]
    }
    bars = {
        "BTCUSDT": {
            "change_bps_ws_1h": 250,
            "ws_bar_1h_trade_count": 8,
        }
    }
    out, n, skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert n == 1
    assert not skip  # 15m already present
    row = out["spot"][0]
    assert row["change_bps_1h"] == 250
    assert row["change_bps_1h_source"] == "native_ws_bar_1h"
    assert row["htf_source"] == "native_ws_bar_1h"


def test_sparse_1h_does_not_fill() -> None:
    uni = {"spot": [{"symbol": "ETHUSDT", "price_i": 1}]}
    bars = {
        "ETHUSDT": {
            "change_bps_ws_1h": 99,
            "ws_bar_1h_trade_count": 1,
        }
    }
    out, _n, _skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert out["spot"][0].get("change_bps_1h") is None


def test_dense_4h_and_1h_htf_label() -> None:
    uni = {
        "spot": [
            {
                "symbol": "BTCUSDT",
                "price_i": 1,
                "change_bps_15m": 10,
                "mtf_source": "native_ws",
            }
        ]
    }
    bars = {
        "BTCUSDT": {
            "change_bps_ws_1h": 100,
            "ws_bar_1h_trade_count": 5,
            "change_bps_ws_4h": 400,
            "ws_bar_4h_trade_count": 12,
        }
    }
    out, n, _skip = stamp_ws_bar_metrics(uni, bars, promote_to_mtf=True)
    assert n == 1
    row = out["spot"][0]
    assert row["change_bps_1h"] == 100
    assert row["change_bps_4h"] == 400
    assert row["change_bps_4h_source"] == "native_ws_bar_4h"
    assert row["htf_source"] == "native_ws_bar_htf"
