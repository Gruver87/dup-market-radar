#!/usr/bin/env python3
"""Static honesty gate — needle freeze for industrial companion posture.

Exit 0 only when required strings/config needles are present. Does not claim
soak or mainnet. Run: python scripts/radar_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FAILURES: list[str] = []


def need(path: Path, needle: str, label: str) -> None:
    if not path.is_file():
        FAILURES.append(f"missing_file:{label}:{path.name}")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if needle not in text:
        FAILURES.append(f"missing_needle:{label}:{needle[:64]}")


def main() -> int:
    cfg = ROOT / "config" / "default.yaml"
    need(cfg, "deployment_mode:", "deployment_mode")
    need(cfg, "secrets:", "secrets_block")
    need(cfg, "backend: env", "secrets_backend_env")
    need(cfg, "recovery:", "recovery_block")
    need(cfg, "prefer: true", "native_prefer")
    need(cfg, "fail_closed: true", "native_fail_closed")
    need(cfg, "dry_run: true", "auto_dry_run_default")

    need(ROOT / "src" / "radar" / "secret_mgmt" / "ports.py", "SecretManagerPort", "secret_port")
    need(
        ROOT / "src" / "radar" / "secret_mgmt" / "factory.py",
        "SecretBackendRefused",
        "secret_prod_refuse",
    )
    need(
        ROOT / "src" / "radar" / "observability" / "ports.py",
        "MetricsExporterPort",
        "metrics_port",
    )
    need(ROOT / "src" / "radar" / "recovery.py", "RecoverySupervisor", "recovery")
    need(ROOT / "docs" / "EVIDENCE_MATRIX.md", "approx_48h_claim_ok", "evidence_matrix")
    need(ROOT / "docs" / "ARCHITECTURE_BLUEPRINT.md", "Column assembly", "architecture_blueprint")
    need(ROOT / "docs" / "PROFILES.md", "python-lab", "profiles")
    need(ROOT / "docs" / "PROFILES.md", "ci-native", "profiles_ci_native")
    need(ROOT / "docs" / "DISCLAIMER.md", "Not financial advice", "disclaimer")
    # CI honesty: both workflows must exist; README must not deny ci-native.
    need(ROOT / ".github" / "workflows" / "ci.yml", "python-lab-only", "ci_python_lab")
    need(ROOT / ".github" / "workflows" / "ci-native.yml", "maturin", "ci_native_workflow")
    need(ROOT / "README.md", "ci-native.yml", "readme_ci_native")
    readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
    if "Maturin native CI is not in this pin" in readme:
        FAILURES.append("readme_denies_ci_native")

    # CORS never *
    api_app = (ROOT / "src" / "radar" / "api" / "app.py").read_text(
        encoding="utf-8", errors="replace"
    )
    if 'allow_origins=["*"]' in api_app or "allow_origins=['*']" in api_app:
        FAILURES.append("cors_star_hardcoded")

    # run_radar: never bare --reload on whole tree (causes .venv / ~adar_native cascades)
    run_radar = (ROOT / "scripts" / "run_radar.ps1").read_text(
        encoding="utf-8", errors="replace"
    )
    need(ROOT / "scripts" / "run_radar.ps1", "--reload-dir", "run_radar_scoped_reload")
    need(ROOT / "scripts" / "run_radar.ps1", "~adar_native", "run_radar_leftover_cleanup")
    if "--reload" in run_radar and "--reload-dir" not in run_radar:
        FAILURES.append("run_radar_bare_reload")
    if "build_native.ps1" in run_radar and "maturin develop" in run_radar.lower():
        FAILURES.append("run_radar_rebuilds_native")

    if FAILURES:
        print("radar_gate FAIL")
        for f in FAILURES[:32]:
            print(" ", f)
        return 1
    print("radar_gate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
