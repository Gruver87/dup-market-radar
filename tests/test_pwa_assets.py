"""PWA shell assets — installable panel; API must not be SW-cached."""

from pathlib import Path

from fastapi.testclient import TestClient

from radar.api.app import DASHBOARD, app


def test_pwa_files_on_disk():
    assert (DASHBOARD / "manifest.webmanifest").is_file()
    assert (DASHBOARD / "sw.js").is_file()
    assert (DASHBOARD / "icons" / "icon-192.png").stat().st_size > 100
    assert (DASHBOARD / "icons" / "icon-512.png").stat().st_size > 100
    sw = (DASHBOARD / "sw.js").read_text(encoding="utf-8")
    assert "/api/" in sw
    assert "never" in sw.lower() or "Never" in sw


def test_pwa_routes():
    client = TestClient(app)
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200
    assert "standalone" in m.text
    assert "Radar" in m.text
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "Service-Worker-Allowed" in sw.headers
    assert sw.headers.get("Service-Worker-Allowed") == "/"
    assert "dup-radar-shell" in sw.text
