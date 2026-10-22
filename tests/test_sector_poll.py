"""Sector ingest cadence planner (0.2.48)."""

from radar.config import load_config
from radar.ingest.sector_poll import (
    SectorPollState,
    effective_snapshot_refresh_ms,
    group_poll_ms,
    group_poll_sec,
    sector_poll_sec,
)


def test_group_poll_is_min_of_drivers():
    cfg = load_config()
    assert sector_poll_sec(cfg, "spot_alts") == 60
    assert sector_poll_sec(cfg, "futures") == 45
    assert group_poll_sec(cfg, "cex") == 45
    assert group_poll_sec(cfg, "memes") == 45  # aligned with snapshot_refresh_ms (0.2.69)
    assert group_poll_sec(cfg, "stables") == 60


def test_due_groups_force_and_cache():
    cfg = load_config()
    st = SectorPollState()
    assert st.due_groups(cfg, now_ms=1_000_000) == {"cex", "memes", "stables"}
    st.store_cex(spot=[{"s": 1}], futures=[], listings=[], whales=[], now_ms=1_000_000)
    st.store_memes([{"m": 1}], now_ms=1_000_000)
    st.store_stables([{"t": 1}], now_ms=1_000_000)
    # Within all polls
    assert st.due_groups(cfg, now_ms=1_000_000 + 10_000) == set()
    # Memes + cex both 45s (whales/futures drive cex min)
    assert st.due_groups(cfg, now_ms=1_000_000 + 31_000) == set()
    assert st.due_groups(cfg, now_ms=1_000_000 + 46_000) == {"cex", "memes"}
    assert st.due_groups(cfg, now_ms=1_000_000 + 10_000, force=True) == {
        "cex",
        "memes",
        "stables",
    }
    cached = st.cached_slices()
    assert cached["spot"] == [{"s": 1}]
    assert cached["memes"] == [{"m": 1}]


def test_effective_snapshot_refresh_tracks_fastest_group():
    cfg = load_config()
    # fastest group poll is 45s (cex/memes); outer cache must not sleep longer
    assert effective_snapshot_refresh_ms(cfg, 60_000) == group_poll_ms(cfg, "memes")
    assert effective_snapshot_refresh_ms(cfg, 45_000) == 45_000
    assert effective_snapshot_refresh_ms(cfg, 10_000) == 10_000
    assert effective_snapshot_refresh_ms(cfg, 1_000) == 5_000  # floor
