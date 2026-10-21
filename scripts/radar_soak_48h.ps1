# 48h live soak — operator host only. Writes data/evidence/soak_48h_*.json
# when duration≈48h and hard_fails=0. Not financial advice.
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
$env:PYTHONPATH = "src"

Write-Host "Starting soak Hours=$Hours IntervalSec=$IntervalSec (approx cycles=$(([int]($Hours*3600/$IntervalSec))))"
python scripts/radar_soak_lab.py --duration-hours $Hours --interval-sec $IntervalSec --retain-keep $RetainKeep
exit $LASTEXITCODE
