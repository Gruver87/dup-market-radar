"""Isolate pytest from operator `.env` (RADAR_API_KEY / REQUIRE) on the host."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_operator_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed lab defaults for tests — do not inherit Desktop `.env` keys.

    Individual tests may still ``setenv`` RADAR_API_KEY / REQUIRE as needed.
    """
    monkeypatch.setenv("RADAR_DISABLE_DOTENV", "1")
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_SNAPSHOT_REQUIRE_API_KEY", raising=False)
    try:
        from radar.secret_mgmt import reset_secret_manager_for_tests

        reset_secret_manager_for_tests()
    except Exception:  # noqa: BLE001
        pass
