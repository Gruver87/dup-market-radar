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


def need_exists(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        FAILURES.append(f"missing_file:{label}:{path.name}")


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
    need(ROOT / "docs" / "EVIDENCE_MATRIX.md", "Verify Full", "evidence_verify_full")
    need(ROOT / "docs" / "EVIDENCE_MATRIX.md", "Verify Quick", "evidence_verify_quick")
    need(ROOT / "docs" / "ARCHITECTURE_BLUEPRINT.md", "Column assembly", "architecture_blueprint")
    need(ROOT / "docs" / "PROFILES.md", "python-lab", "profiles")
    need(ROOT / "docs" / "PROFILES.md", "ci-native", "profiles_ci_native")
    need(ROOT / "docs" / "DISCLAIMER.md", "Not financial advice", "disclaimer")
    need(ROOT / "docs" / "OPS_READY.md", "ops_ready", "ops_ready_doc")
    need(ROOT / "docs" / "LIVE_MONEY.md", "dry_run: false", "live_money")
    need(ROOT / "docs" / "LIVE_MONEY.md", "go_live_money.ps1", "live_money_script_doc")
    need(ROOT / "scripts" / "go_ready.ps1", "GO_READY", "go_ready_script")
    need(ROOT / "scripts" / "go_live_money.ps1", "GO_LIVE_MONEY", "go_live_money_script")
    need(ROOT / "config" / "live_money.yaml", "dry_run: false", "live_money_overlay")
    need(ROOT / "config" / "live_money.yaml", "spot_alts", "live_money_spot_only")
    need(ROOT / "docs" / "grafana" / "prometheus-scrape.yml", "metrics_path", "grafana_scrape")
    need(ROOT / "docs" / "grafana" / "radar-ops.json", "dup-radar-ops", "grafana_dashboard")
    need(ROOT / "docs" / "PWA.md", "remote panel", "pwa_doc")
    need(ROOT / "docs" / "AGENT_SKILLS.md", "dup-agent-skills", "agent_skills_doc")
    need(ROOT / "docs" / "AGENT_SKILLS.md", "no-futures-auto", "agent_skills_futures_refuse")
    need(ROOT / "scripts" / "link_agent_skills.ps1", "link_agent_skills", "link_agent_skills_script")
    need(ROOT / "README.md", "dup-agent-skills", "readme_agent_skills")
    need(ROOT / "docs" / "OPS.md", "AGENT_SKILLS", "ops_agent_skills")
    need(ROOT / "dashboard" / "manifest.webmanifest", "standalone", "pwa_manifest")
    need(ROOT / "dashboard" / "sw.js", "dup-radar-shell", "pwa_sw")
    need_exists(ROOT / "dashboard" / "icons" / "icon-192.png", "pwa_icon_192")
    need_exists(ROOT / "dashboard" / "icons" / "icon-512.png", "pwa_icon_512")
    need(ROOT / "src" / "radar" / "api" / "app.py", "/sw.js", "pwa_sw_route")
    need(ROOT / "src" / "radar" / "api" / "app.py", "manifest.webmanifest", "pwa_manifest_route")

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
    need(ROOT / "scripts" / "run_radar.ps1", "run_radar_server.py", "run_radar_quiet_entry")
    need(ROOT / "scripts" / "run_radar.ps1", "--reload-dir", "run_radar_scoped_reload")
    need(ROOT / "scripts" / "run_radar.ps1", "~adar_native", "run_radar_leftover_cleanup")
    need(
        ROOT / "scripts" / "run_radar_server.py",
        "_ShutdownNoiseFilter",
        "run_radar_shutdown_filter",
    )
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
