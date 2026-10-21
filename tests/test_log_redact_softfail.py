"""Soft-fail / native error paths must redact secrets in messages (0.2.42)."""

import logging

import pytest

from radar.secrets_redact import REDACTED, register_secrets
from radar.score.engine import NativeScoreError, score_sectors


def test_native_exception_redacted_in_fail_closed(monkeypatch):
    register_secrets(["sekrit-token-xyz"])
    from radar.score import engine as eng

    monkeypatch.setattr(eng, "native_available", lambda: True)

    def boom(*_a, **_k):
        raise RuntimeError("boom sekrit-token-xyz leaked")

    monkeypatch.setattr(eng, "score_universe_native", boom)
    cfg = {"native": {"prefer": True, "fail_closed": True}, "top_n": 3, "sectors": {}}
    empty = {
        "spot": [],
        "futures": [],
        "memes": [],
        "stables": [],
        "listings": [],
        "whales": [],
    }
    with pytest.raises(NativeScoreError) as ei:
        score_sectors(empty, cfg, top_n=3)
    msg = str(ei.value)
    assert "sekrit-token-xyz" not in msg
    assert REDACTED in msg


def test_dotenv_read_failure_redacts(caplog, tmp_path, monkeypatch):
    from pathlib import Path

    from radar.trade import env_load

    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    register_secrets(["sekrit-token-xyz"])
    orig = Path.read_text

    def selective(self, *a, **k):
        if self.name == ".env":
            raise OSError("denied sekrit-token-xyz")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", selective)
    with caplog.at_level(logging.WARNING, logger="radar.trade.env"):
        env_load.load_dotenv(env)
    joined = " ".join(r.message for r in caplog.records)
    assert "dotenv read failed" in joined
    assert "sekrit-token-xyz" not in joined
