# DUP Market Radar - unified verification entry (operator).
#
# Profiles:
#   Quick    - pytest + smoke offline (no network, no native rebuild)
#   Lab      - Quick + live smoke --evidence (needs network)
#   PreSoak  - native build + Lab + short soak (before 48h)
#   Soak48h  - PreSoak (unless -SkipPreSoak) + 48h wall-clock soak
#   Native   - build_native.ps1 only
#   List     - show profiles and exit
#
# Usage:
#   .\scripts\verify.ps1
#   .\scripts\verify.ps1 -Profile Quick
#   .\scripts\verify.ps1 -Profile Lab
#   .\scripts\verify.ps1 -Profile PreSoak
#   .\scripts\verify.ps1 -Profile PreSoak -SkipBuild
#   .\scripts\verify.ps1 -Profile Soak48h
#   .\scripts\verify.ps1 -Profile Soak48h -SkipPreSoak
#
# Exit 1 on hard fail. 48h claim only if pack approx_48h_claim_ok=true.
# Not financial advice. See docs/DISCLAIMER.md.
param(
    [ValidateSet("Quick", "Lab", "PreSoak", "Soak48h", "Native", "List")]
    [string]$Profile = "Lab",
    [switch]$SkipBuild,
    [switch]$SkipPreSoak,
    [int]$SoakCycles = 3,
    [double]$SoakIntervalSec = 45,
    [double]$Soak48Hours = 48,
    [double]$Soak48IntervalSec = 300,
    [int]$RetainKeep = 40
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-RadarPython {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    return "python"
}

function Require-Venv {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Error ".venv missing - run: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt"
    }
}

function Step([string]$name) {
    Write-Host ""
    Write-Host ("=== {0} ===" -f $name)
}

function Invoke-NativeBuild {
    param([switch]$Skip)
    Step "native"
    if ($Skip) {
        Write-Host "SkipBuild - import check only"
    } else {
        & (Join-Path $Root "scripts\build_native.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $py = Get-RadarPython
    & $py -c "import radar_native; print('radar_native', radar_native.native_version())"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Pytest {
    Step "pytest"
    $py = Get-RadarPython
    $env:PYTHONPATH = "src"
    & $py -m pytest -q --tb=line
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-SmokeOffline {
    Step "smoke offline"
    $py = Get-RadarPython
    $env:PYTHONPATH = "src"
    & $py scripts/radar_smoke_lab.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-SmokeLive {
    Step "smoke live --evidence"
    $py = Get-RadarPython
    $env:PYTHONPATH = "src"
    & $py scripts/radar_smoke_lab.py --live --evidence
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-ShortSoak {
    Step ("short soak ({0} cycles, interval {1}s)" -f $SoakCycles, $SoakIntervalSec)
    $py = Get-RadarPython
    $env:PYTHONPATH = "src"
    & $py scripts/radar_soak_lab.py --cycles $SoakCycles --interval-sec $SoakIntervalSec --retain-keep $RetainKeep
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Soak48h {
    Step ("48h soak (Hours={0} IntervalSec={1})" -f $Soak48Hours, $Soak48IntervalSec)
    Write-Host "Claim 48h ONLY if pack approx_48h_claim_ok=true and hard_fails=0."
    $py = Get-RadarPython
    $env:PYTHONPATH = "src"
    & $py -c "import radar_native" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "radar_native not importable - soak will soft-prefer Python (lab)."
    }
    & $py scripts/radar_soak_lab.py --duration-hours $Soak48Hours --interval-sec $Soak48IntervalSec --retain-keep $RetainKeep
    exit $LASTEXITCODE
}

function Show-List {
    Write-Host "DUP Market Radar - verify profiles"
    Write-Host ""
    Write-Host "  Quick     pytest + smoke offline"
    Write-Host "  Lab       Quick + live smoke --evidence          (default)"
    Write-Host "  PreSoak   native + Lab + short soak              (before 48h)"
    Write-Host "  Soak48h   PreSoak then 48h wall-clock"
    Write-Host "  Native    build_native only"
    Write-Host "  List      this help"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\scripts\verify.ps1 -Profile Quick"
    Write-Host "  .\scripts\verify.ps1 -Profile Lab"
    Write-Host "  .\scripts\verify.ps1 -Profile PreSoak"
    Write-Host "  .\scripts\verify.ps1 -Profile PreSoak -SkipBuild"
    Write-Host "  .\scripts\verify.ps1 -Profile Soak48h"
    Write-Host ""
    Write-Host "Evidence: data/evidence/ (gitignored). See docs/DISCLAIMER.md"
}

if ($Profile -eq "List") {
    Show-List
    exit 0
}

Require-Venv
$env:PYTHONPATH = "src"
Write-Host ("DUP Market Radar verify Profile={0} Root={1}" -f $Profile, $Root)

switch ($Profile) {
    "Native" {
        Invoke-NativeBuild -Skip:$SkipBuild
        Write-Host ""
        Write-Host "VERIFY Native: PASS"
        exit 0
    }
    "Quick" {
        Invoke-Pytest
        Invoke-SmokeOffline
        Write-Host ""
        Write-Host "VERIFY Quick: PASS"
        exit 0
    }
    "Lab" {
        Invoke-Pytest
        Invoke-SmokeOffline
        Invoke-SmokeLive
        Write-Host ""
        Write-Host "VERIFY Lab: PASS"
        Write-Host "Next before 48h: .\scripts\verify.ps1 -Profile PreSoak"
        exit 0
    }
    "PreSoak" {
        Invoke-NativeBuild -Skip:$SkipBuild
        Invoke-Pytest
        Invoke-SmokeOffline
        Invoke-SmokeLive
        Invoke-ShortSoak
        Write-Host ""
        Write-Host "VERIFY PreSoak: PASS"
        Write-Host "Next (operator): .\scripts\verify.ps1 -Profile Soak48h"
        Write-Host "Do NOT claim 48h until pack approx_48h_claim_ok=true."
        exit 0
    }
    "Soak48h" {
        if (-not $SkipPreSoak) {
            Invoke-NativeBuild -Skip:$SkipBuild
            Invoke-Pytest
            Invoke-SmokeOffline
            Invoke-SmokeLive
            Invoke-ShortSoak
            Write-Host "PreSoak segment PASS - starting 48h..."
        } else {
            Write-Warning "SkipPreSoak - assuming prior PreSoak PASS on this host."
        }
        Invoke-Soak48h
    }
    default {
        Write-Error ("unknown profile: {0}" -f $Profile)
        exit 1
    }
}
