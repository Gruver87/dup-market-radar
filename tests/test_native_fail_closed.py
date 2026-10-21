import pytest

from radar.score.engine import NativeScoreError, score_sectors


def test_prefer_fail_closed_raises_when_native_forced_missing(monkeypatch):
    import radar.score.engine as eng

    monkeypatch.setattr(eng, "native_available", lambda: False)
    cfg = {
        "native": {"prefer": True, "fail_closed": True},
        "stable_bases": [],
        "meme_keywords": [],
        "sectors": {},
    }
    with pytest.raises(NativeScoreError):
        score_sectors({"spot": [], "futures": [], "memes": [], "stables": [], "listings": [], "whales": []}, cfg, 5)


def test_prefer_soft_fallback_label(monkeypatch):
    import radar.score.engine as eng

    monkeypatch.setattr(eng, "native_available", lambda: False)
    cfg = {
        "native": {"prefer": True, "fail_closed": False},
        "stable_bases": [],
        "meme_keywords": [],
        "sectors": {
            "spot_alts": {},
            "futures": {},
            "memes": {},
            "stables": {},
            "listings": {},
            "whales": {},
        },
    }
    sectors, backend = score_sectors(
        {"spot": [], "futures": [], "memes": [], "stables": [], "listings": [], "whales": []},
        cfg,
        5,
    )
    assert backend == "python_native_missing"
    assert "spot_alts" in sectors
