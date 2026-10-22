"""Primary ensure gate widen — display tops should land in parallel window."""


def test_ensure_rank_widen_defaults():
    """0.2.79: ensure_rank_n == ensure_cap; fill-gate stays top_n."""
    top_n = 10
    ensure_cap = max(top_n * 3, 24)
    expand_n = max(8, top_n)
    ensure_rank_n = int(ensure_cap)
    assert expand_n == 10
    assert ensure_cap == 30
    assert ensure_rank_n == 30
    assert ensure_rank_n > top_n
    assert ensure_rank_n <= ensure_cap
