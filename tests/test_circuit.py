from radar.ingest.circuit import (
    MAX_REASON_LEN,
    MAX_TRACKED_VENUES,
    VenueCircuitBreaker,
    _normalize_venue,
    reset_breaker_for_tests,
)


def test_opens_after_threshold():
    reset_breaker_for_tests()
    b = VenueCircuitBreaker(fail_threshold=2, open_ms=60_000)
    assert b.allow("mexc") is True
    b.record_fail("mexc", "boom")
    assert b.allow("mexc") is True
    b.record_fail("mexc", "boom2")
    assert b.allow("mexc") is False
    st = b.status()
    assert "mexc" in st["open_remaining_ms"]
    b.record_ok("mexc")
    assert b.allow("mexc") is True


def test_ok_resets_fails():
    b = VenueCircuitBreaker(fail_threshold=3, open_ms=1000)
    b.record_fail("binance")
    b.record_fail("binance")
    b.record_ok("binance")
    b.record_fail("binance")
    assert b.allow("binance") is True


def test_normalize_venue_sanitizes():
    assert _normalize_venue('evil"venue\n') == "evil_venue"
    assert _normalize_venue("") == "unknown"
    assert len(_normalize_venue("x" * 200)) == 64


def test_tracked_venues_soft_cap():
    b = VenueCircuitBreaker(fail_threshold=1, open_ms=60_000)
    for i in range(MAX_TRACKED_VENUES + 10):
        b.record_fail(f"v{i}", reason="x" * 500)
    st = b.status()
    assert st["tracked_venues"] == MAX_TRACKED_VENUES
    # Overflow key never opens
    assert b.allow(f"v{MAX_TRACKED_VENUES}") is True
    # Cap-resident still opens
    assert b.allow("v0") is False


def test_reason_truncated_in_log(caplog):
    import logging

    b = VenueCircuitBreaker(fail_threshold=1, open_ms=1000)
    with caplog.at_level(logging.WARNING, logger="radar.circuit"):
        b.record_fail("kucoin", reason="R" * 500)
    joined = " ".join(r.message for r in caplog.records)
    assert "R" * MAX_REASON_LEN in joined or "reason=" in joined
    assert "R" * (MAX_REASON_LEN + 50) not in joined
