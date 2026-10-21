from radar.persist import atomic_write_json, checksum_path, read_json_object


def test_roundtrip_checksum_ok(tmp_path):
    path = tmp_path / "auto_positions.json"
    digest = atomic_write_json(path, {"SOLUSDT": {"base_size_i": 1}})
    assert checksum_path(path).exists()
    data, err, meta = read_json_object(path)
    assert err is None
    assert data["SOLUSDT"]["base_size_i"] == 1
    assert meta["integrity"] == "ok"
    assert meta["sha256"] == digest


def test_mismatch_fails_closed(tmp_path):
    path = tmp_path / "auto_positions.json"
    atomic_write_json(path, {"A": 1})
    # Valid-length hex that won't match payload
    checksum_path(path).write_text("ab" * 32 + "\n", encoding="utf-8")
    data, err, meta = read_json_object(path)
    assert data == {}
    assert err is not None and "mismatch" in err
    assert meta["integrity"] == "mismatch"


def test_checksum_sidecar_too_large(tmp_path):
    path = tmp_path / "auto_positions.json"
    atomic_write_json(path, {"A": 1})
    checksum_path(path).write_bytes(b"x" * 600)
    data, err, meta = read_json_object(path)
    assert data == {}
    assert err is not None and "too large" in err
    assert meta["integrity"] == "corrupt"


def test_missing_checksum_allowed(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('{"x": 1}', encoding="utf-8")
    data, err, meta = read_json_object(path)
    assert err is None
    assert data == {"x": 1}
    assert meta["integrity"] == "missing_checksum"
    assert meta["sha256"] is not None
    assert len(meta["sha256"]) == 64


def test_json_too_large_refused(tmp_path):
    path = tmp_path / "huge.json"
    path.write_bytes(b"{" + (b"a" * 2000) + b"}")
    data, err, meta = read_json_object(path, max_bytes=512)
    assert data == {}
    assert err is not None and "too large" in err
    assert meta["integrity"] == "too_large"
