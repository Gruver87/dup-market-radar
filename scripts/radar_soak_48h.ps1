# 48h live soak — operator host only. Writes data/evidence/soak_48h_*.json
# when duration≈48h and hard_fails=0. Not financial advice.
# Run .\scripts\pre_soak_gate.ps1 FIRST (native + smoke + short soak).
# Usage:
#   .\scripts\radar_soak_48h.ps1
#   .\scripts\radar_soak_48h.ps1 -IntervalSec 300
param(
    [double]$IntervalSec = 300,
    [double]$Hours = 48,
    [int]$RetainKeep = 40
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONPATH = "src"

& $py -c "import radar_native" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "radar_native not importable — 48h will soft-prefer Python (lab). Prefer: .\scripts\pre_soak_gate.ps1"
}

Write-Host "Starting soak Hours=$Hours IntervalSec=$IntervalSec (approx cycles=$(([int]($Hours*3600/$IntervalSec))))"
& $py scripts/radar_soak_lab.py --duration-hours $Hours --interval-sec $IntervalSec --retain-keep $RetainKeep
exit $LASTEXITCODE
