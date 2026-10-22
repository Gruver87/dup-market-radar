# DUP Market Radar — full local check (NO soak / NO 48h).
#
# Runs: radar_gate → ruff → pytest → smoke offline → warm smoke → live smoke --evidence
#
# Usage (from repo root):
#   .\scripts\test_all.ps1
#   .\scripts\test_all.ps1 -SkipWarm          # skip ~1–2 min warm TTL waits
#   .\scripts\test_all.ps1 -SkipLive          # offline only (+ gate/pytest)
#   .\scripts\test_all.ps1 -SkipBuild         # do not rebuild native (import check only)
#
# Same stack via verify:
#   .\scripts\verify.ps1 -Profile Full
#   .\scripts\verify.ps1 -Profile List
#
# Before money / soak: .\scripts\verify.ps1 -Profile PreSoak
# Not financial advice. See docs/DISCLAIMER.md.
param(
    [switch]$SkipWarm,
    [switch]$SkipLive,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$verify = Join-Path $Root "scripts\verify.ps1"
if (-not (Test-Path $verify)) {
    Write-Error "missing scripts\verify.ps1"
}

$extra = @()
if ($SkipBuild) { $extra += "-SkipBuild" }

# Full profile lives in verify.ps1; pass through skip flags via env for warm/live.
if ($SkipWarm) { $env:RADAR_VERIFY_SKIP_WARM = "1" } else { Remove-Item Env:RADAR_VERIFY_SKIP_WARM -ErrorAction SilentlyContinue }
if ($SkipLive) { $env:RADAR_VERIFY_SKIP_LIVE = "1" } else { Remove-Item Env:RADAR_VERIFY_SKIP_LIVE -ErrorAction SilentlyContinue }

Write-Host "DUP Market Radar test_all → verify -Profile Full"
& $verify -Profile Full @extra
exit $LASTEXITCODE
