from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evidence_retention import retain  # noqa: E402


def test_retain_keeps_newest(tmp_path, monkeypatch):
    import evidence_retention as er

    monkeypatch.setattr(er, "EV_DIR", tmp_path)
    for i in range(5):
        p = tmp_path / f"smoke_live_2026010{i}T000000Z.json"
        p.write_text("{}", encoding="utf-8")
        # distinct mtimes
        import os, time
        os.utime(p, (time.time() - (5 - i), time.time() - (5 - i)))
    (tmp_path / "LATEST.json").write_text("{}", encoding="utf-8")
    out = retain(keep=2)
    assert out["ok"] is True
    remaining = {p.name for p in tmp_path.glob("*.json")}
    assert "LATEST.json" in remaining
    assert len([n for n in remaining if n.startswith("smoke_")]) == 2
