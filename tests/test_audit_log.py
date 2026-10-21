"""Rotating JSONL audit log."""

from pathlib import Path

from radar.audit_log import append_jsonl, rotate_jsonl


def test_append_and_rotate(tmp_path: Path):
    path = tmp_path / "auto_trade_log.jsonl"
    append_jsonl(path, {"n": 1}, max_bytes=40, keep=2)
    assert path.exists()
    path.write_text("x" * 50, encoding="utf-8")
    rotated = rotate_jsonl(path, max_bytes=40, keep=2)
    assert rotated is not None
    assert rotated.name.endswith(".1")
    assert not path.exists()
    assert (tmp_path / "auto_trade_log.jsonl.1").exists()


def test_append_creates_parent(tmp_path: Path):
    path = tmp_path / "nested" / "log.jsonl"
    append_jsonl(path, {"ok": True}, max_bytes=1_000_000, keep=3)
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip()
    assert '"ok": true' in line or '"ok":true' in line.replace(" ", "")


def test_keep_prunes_old_rotations(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    path.write_text("a" * 100, encoding="utf-8")
    for i in range(1, 4):
        (tmp_path / f"audit.jsonl.{i}").write_text(f"old{i}", encoding="utf-8")
    rotate_jsonl(path, max_bytes=50, keep=2)
    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl.2").exists()
    # .3 may have been deleted when keep=2 during shift of .2→.3 then prune of keep
    # After rotate with keep=2: current→.1, old.1→.2, old.2 deleted at i==keep before shift
    assert not (tmp_path / "audit.jsonl.3").exists()


def test_append_truncates_huge_line(tmp_path: Path):
    from radar.audit_log import MAX_AUDIT_LINE_CHARS

    path = tmp_path / "huge.jsonl"
    append_jsonl(path, {"blob": "Z" * (MAX_AUDIT_LINE_CHARS + 1000)}, max_bytes=10_000_000)
    text = path.read_text(encoding="utf-8")
    assert "_audit_truncated" in text
    assert len(text) < MAX_AUDIT_LINE_CHARS
    assert "Z" * 1000 not in text
