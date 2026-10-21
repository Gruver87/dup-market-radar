from radar.ops import bubbles as bub


def test_bubbles_stale_circuit_ok_false(monkeypatch):
    bub._cache = {
        "ok": True,
        "coins": [{"id": "bitcoin"}],
        "generated_at_ms": 1,
    }
    bub._cache_at_ms = 0
    monkeypatch.setattr(bub._circuit, "allow", lambda *_a, **_k: False)
    out = bub.get_bubbles(top_n=10, cache_ttl_ms=1, force=True)
    assert out["ok"] is False
    assert out.get("stale") is True
