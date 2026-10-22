"""SECTOR_META must not claim multi-TF that scorers do not use (0.2.44+)."""

from radar.config import load_config
from radar.pipeline import SECTOR_META, sector_meta_for


def test_sector_meta_no_fake_mtf_claim():
    for key, meta in SECTOR_META.items():
        hint = str(meta.get("poll_hint") or "").lower()
        assert "poll_sec" in hint, key
        # Forbidden: imply full-universe MTF without sampling honesty
        if "15m/1h/4h" in hint and "sampled" not in hint and "no 15m" not in hint:
            raise AssertionError(f"{key}: fake full MTF claim: {hint}")
        assert "context via 24h" not in hint, key


def test_spot_alts_states_24h_and_sampled_short_windows():
    hint = SECTOR_META["spot_alts"]["poll_hint"].lower()
    assert "24h" in hint
    assert "15m" in hint
    assert "1h" in hint
    assert "4h" in hint
    assert "sampled" in hint or "top-n" in hint
    assert "vol" in hint and "median" in hint


def test_futures_states_sampled_short_windows():
    hint = SECTOR_META["futures"]["poll_hint"].lower()
    assert "15m" in hint and "1h" in hint and "4h" in hint
    assert "sampled" in hint or "top-n" in hint
    assert "funding" in hint


def test_memes_states_dex_mtf_fields():
    hint = SECTOR_META["memes"]["poll_hint"].lower()
    method = SECTOR_META["memes"]["method"].lower()
    assert "h24" in hint or "h24" in method
    assert "h1" in hint or "h1" in method
    assert "m5" in hint or "m5" in method
    assert "no extra candle" in method or "no extra" in method


def test_sector_meta_for_injects_poll_sec():
    cfg = load_config()
    meta = sector_meta_for(cfg)
    assert meta["memes"]["group"] == "memes"
    assert meta["memes"]["poll_sec"] == 45
    assert meta["memes"]["group_poll_sec"] == 45
    assert meta["whales"]["group"] == "cex"
    assert "bundled with cex" in meta["whales"]["poll_hint"].lower()
    # cex group = min(spot 60, futures 45, listings 60) = 45
    assert meta["futures"]["group_poll_sec"] == 45
    assert "45s" in meta["spot_alts"]["poll_hint"]
