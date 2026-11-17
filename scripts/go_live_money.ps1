# DUP Market Radar — live-money gate (spot only; futures auto never).
#
# Fail-closed: refuses without .env KuCoin + RADAR_API_KEY, ops_ready, native.
# Does NOT start 48h soak. Does NOT enable Auto ON (enabled_default stays false).
# After PASS: run with RADAR_PROFILE=live-money then flip Auto ON in dashboard.
#
# Not financial advice — docs/DISCLAIMER.md / docs/LIVE_MONEY.md
param(
    [switch]$SkipOpsReadyCheck
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "DUP Market Radar go_live_money"
Write-Host "Root: $Root"
Write-Host "Futures auto: OFF (hard). Soak48h: not this script."
Write-Host ""

$fail = @()

# --- .env presence (never echo values) ---
$envPath = Join-Path $Root ".env"
if (-not (Test-Path $envPath)) {
    $fail += "missing .env — copy .env.example and fill KuCoin Spot + RADAR_API_KEY"
} else {
    $map = @{}
    Get-Content $envPath | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $parts = $_ -split '=', 2
        $n = $parts[0].Trim()
        $v = if ($parts.Count -gt 1) { $parts[1].Trim().Trim('"').Trim("'") } else { "" }
        if ($n) { $map[$n] = $v }
    }
    foreach ($k in @("KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE", "RADAR_API_KEY")) {
        if (-not $map.ContainsKey($k) -or [string]::IsNullOrWhiteSpace($map[$k])) {
            $fail += "missing/empty $k in .env"
        }
    }
    $req = $map["RADAR_REQUIRE_API_KEY"]
    if ($req -and $req -notin @("1", "true", "TRUE", "yes", "YES")) {
        $fail += "RADAR_REQUIRE_API_KEY should be 1 for live-money"
    }
}

# --- ops-ready pack ---
if (-not $SkipOpsReadyCheck) {
    $readyPath = Join-Path $Root "data\evidence\READY_OPS.json"
    if (-not (Test-Path $readyPath)) {
        $fail += "missing data/evidence/READY_OPS.json — run .\scripts\go_ready.ps1 first"
    } else {
        $ready = Get-Content $readyPath -Raw | ConvertFrom-Json
        if (-not $ready.ops_ready) {
            $fail += "READY_OPS.json ops_ready!=true"
        }
        if ([int]($ready.short_soak_hard_fails) -ne 0) {
            $fail += "READY_OPS short_soak_hard_fails!=0"
        }
    }
}

# --- native wheel ---
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$nativeOk = & $py -c "import radar_native; print(getattr(radar_native,'__version__','?'))" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $nativeOk) {
    $fail += "radar_native import failed — .\scripts\build_native.ps1"
} else {
    Write-Host "native: $nativeOk"
}

# --- profile loads + futures excluded ---
$env:RADAR_PROFILE = "live-money"
$env:PYTHONPATH = "src"
$check = & $py -c @"
from radar.config import load_config
from radar.trade.auto_engine import FUTURES_BLOCK
c = load_config()
a = c.get('auto_trade') or {}
secs = set(a.get('allowed_sectors') or [])
bad = secs & set(FUTURES_BLOCK)
assert c.get('_radar_profile') == 'live-money', c.get('_radar_profile')
assert a.get('dry_run') is False
assert a.get('enabled_default') is False
assert not bad, bad
print('profile=live-money dry_run=false enabled_default=false futures_in_auto=0')
"@
if ($LASTEXITCODE -ne 0) {
    $fail += "live-money profile validation failed"
} else {
    Write-Host $check
}
Remove-Item Env:RADAR_PROFILE -ErrorAction SilentlyContinue

if ($fail.Count -gt 0) {
    Write-Host ""
    Write-Host "GO_LIVE_MONEY: FAIL"
    $fail | ForEach-Object { Write-Host " - $_" }
    Write-Host ""
    Write-Host "Fix .env / go_ready, then re-run. Default lab config stays dry_run:true."
    exit 1
}

Write-Host ""
Write-Host "GO_LIVE_MONEY: PASS (preflight)"
Write-Host "Next:"
Write-Host '  $env:RADAR_PROFILE = "live-money"'
Write-Host '  $env:RADAR_REQUIRE_API_KEY = "1"'
Write-Host "  .\scripts\run_radar.ps1"
Write-Host "  Dashboard: localStorage.setItem('radar_api_key', '<RADAR_API_KEY>')"
Write-Host "  Confirm Watchlist ok=true + mtf_ensure_ok=true, then Auto ON (spot only)."
Write-Host "  Rollback: Auto OFF or unset RADAR_PROFILE (lab dry_run)."
Write-Host "48h soak: later, only after your approval."
exit 0
