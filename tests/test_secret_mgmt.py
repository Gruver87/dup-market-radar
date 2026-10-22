"""SecretManagerPort — env adapter + prod refuse file."""

from __future__ import annotations

import json

import pytest

from radar.secret_mgmt import (
    SECRET_RADAR_API_KEY,
    SecretBackendRefused,
    SecretNotFoundError,
    build_secret_manager,
    reset_secret_manager_for_tests,
)
from radar.secret_mgmt.env_adapter import EnvSecretAdapter
from radar.secret_mgmt.file_adapter import FileSecretAdapter


def test_env_adapter_roundtrip(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.setenv("RADAR_API_KEY", "test-api-key-abcdefgh")
    a = EnvSecretAdapter()
    assert a.has_secret(SECRET_RADAR_API_KEY)
    assert a.get_secret(SECRET_RADAR_API_KEY) == "test-api-key-abcdefgh"
    assert a.backend_name() == "env"


def test_env_missing():
    a = EnvSecretAdapter()
    with pytest.raises(SecretNotFoundError):
        a.get_secret("no.such.secret")


def test_file_adapter(tmp_path):
    p = tmp_path / "secrets.json"
    p.write_text(
        json.dumps({SECRET_RADAR_API_KEY: "file-key-abcdefghijkl"}),
        encoding="utf-8",
    )
    a = FileSecretAdapter(p)
    assert a.get_secret(SECRET_RADAR_API_KEY).startswith("file-key")


def test_prod_refuses_file_backend(monkeypatch):
    reset_secret_manager_for_tests()
    monkeypatch.setenv("RADAR_DEPLOYMENT_MODE", "prod")
    monkeypatch.setenv("RADAR_SECRET_BACKEND", "file")
    with pytest.raises(SecretBackendRefused):
        build_secret_manager()


def test_lab_allows_file(monkeypatch, tmp_path):
    reset_secret_manager_for_tests()
    monkeypatch.setenv("RADAR_DEPLOYMENT_MODE", "lab")
    monkeypatch.setenv("RADAR_SECRET_BACKEND", "file")
    p = tmp_path / "s.json"
    p.write_text(json.dumps({SECRET_RADAR_API_KEY: "lab-file-key-abcdef"}), encoding="utf-8")
    monkeypatch.setenv("RADAR_SECRETS_FILE", str(p))
    mgr = build_secret_manager()
    assert mgr.backend_name() == "file"
    assert mgr.has_secret(SECRET_RADAR_API_KEY)
