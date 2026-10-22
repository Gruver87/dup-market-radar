# Pre-soak gate — run BEFORE 48h soak. Exit 1 on any hard fail.
# Does NOT start 48h. Does NOT claim soak.
#
# Steps:
#   1) build_native.ps1 (radar_native in .venv)
#   2) pytest
#   3) smoke offline + live --evidence
#   4) short soak lab (default 3 cycles)
#
# Usage:
#   .\scripts\pre_soak_gate.ps1
#   .\scripts\pre_soak_gate.ps1 -SkipBuild -SoakCycles 5
param(
    [switch]$SkipBuild,
    [int]$SoakCycles = 3,
    [double]$SoakIntervalSec = 45
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error ".venv missing — create venv or run build_native.ps1 first"
}

Write-Host "=== pre-soak: native ==="
if (-not $SkipBuild) {
    & (Join-Path $Root "scripts\build_native.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "SkipBuild set — checking import only"
}
& $py -c "import radar_native; print('radar_native', radar_native.native_version())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:PYTHONPATH = "src"
Write-Host "=== pre-soak: pytest ==="
& $py -m pytest -q --tb=line
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== pre-soak: smoke offline ==="
& $py scripts/radar_smoke_lab.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== pre-soak: smoke live --evidence ==="
& $py scripts/radar_smoke_lab.py --live --evidence
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== pre-soak: short soak ($SoakCycles cycles) ==="
& $py scripts/radar_soak_lab.py --cycles $SoakCycles --interval-sec $SoakIntervalSec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "PRE_SOAK_GATE: PASS"
Write-Host "Next (operator): .\scripts\radar_soak_48h.ps1"
Write-Host "Do NOT claim 48h until pack approx_48h_claim_ok=true."
exit 0
