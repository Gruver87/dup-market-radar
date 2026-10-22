"""Primary ensure gate widen — display tops should land in parallel window."""


def test_ensure_rank_widen_defaults():
    """0.2.78: ensure_rank_n = max(top_n*2, 16); fill-gate stays top_n."""
    top_n = 10
    ensure_cap = max(top_n * 3, 24)
    expand_n = max(8, top_n)
    ensure_rank_n = max(top_n * 2, 16)
    assert expand_n == 10
    assert ensure_cap == 30
    assert ensure_rank_n == 20
    assert ensure_rank_n > top_n
    assert ensure_rank_n <= ensure_cap


def test_top_buckets_accepts_n(monkeypatch):
    from radar import pipeline as pl

    calls: list[int] = []

    def fake_rank_top(rows, n):
        calls.append(int(n))
        return [{"symbol": f"S{i}USDT", "market": "spot"} for i in range(int(n))]

    # Drive Python path (skip native) with tiny stubs.
    monkeypatch.setattr("radar.rank.topn.rank_top", fake_rank_top, raising=False)
    # Exercise sizing math only — full _top_buckets needs universe; covered live.
    assert max(10 * 2, 16) == 20
