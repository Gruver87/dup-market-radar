from radar.persist import atomic_write_json, backup_dir, read_json_object


def test_backup_created_and_pruned(tmp_path):
    path = tmp_path / "auto_positions.json"
    atomic_write_json(path, {"n": 1}, backup_keep=2)
    atomic_write_json(path, {"n": 2}, backup_keep=2)
    atomic_write_json(path, {"n": 3}, backup_keep=2)
    atomic_write_json(path, {"n": 4}, backup_keep=2)
    bdir = backup_dir(path)
    backups = list(bdir.glob("auto_positions_*.json"))
    assert 1 <= len(backups) <= 2
    data, err, meta = read_json_object(path)
    assert err is None
    assert data["n"] == 4
    assert meta["integrity"] == "ok"
