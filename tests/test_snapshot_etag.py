from fastapi.testclient import TestClient

from radar.api import app as app_mod


def test_snapshot_etag_304(monkeypatch):
    fake = {
        "ok": True,
        "schema_version": 2,
        "generated_at_ms": 1_700_000_000_000,
        "score_backend": "python",
        "universe_size": 3,
        "sectors": {},
        "sector_meta": {},
        "errors": [],
        "disclaimer": "x",
        "product": "DUP Market Radar",
    }
    monkeypatch.setattr(app_mod, "_get_snapshot", lambda force=False: dict(fake))
    client = TestClient(app_mod.app)
    r1 = client.get("/api/snapshot")
    assert r1.status_code == 200
    etag = r1.headers.get("etag") or r1.headers.get("ETag")
    assert etag
    r2 = client.get("/api/snapshot", headers={"If-None-Match": etag})
    assert r2.status_code == 304
