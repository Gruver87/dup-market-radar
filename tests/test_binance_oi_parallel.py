"""Binance OI fan-out is parallel and soft-fails per symbol."""

from radar.ingest.binance import _fetch_oi_parallel, _OI_SAMPLE_N


class _FakeHttp:
    def __init__(self) -> None:
        self.calls = 0

    def get_json(self, url, *, params=None, use_cache=True):
        self.calls += 1
        sym = (params or {}).get("symbol") or ""
        if sym == "BADUSDT":
            raise RuntimeError("boom")
        return {"openInterest": "12.5"}


def test_fetch_oi_parallel_covers_all_and_soft_fails():
    http = _FakeHttp()
    syms = ["BTCUSDT", "ETHUSDT", "BADUSDT"]
    out = _fetch_oi_parallel(http, syms)
    assert set(out) == set(syms)
    assert out["BTCUSDT"] == "12.5"
    assert out["ETHUSDT"] == "12.5"
    assert out["BADUSDT"] is None
    assert http.calls == 3
    assert _OI_SAMPLE_N == 24
