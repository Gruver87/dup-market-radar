# Non-soak operator board for DUP Market Radar.
# Never starts soak / never invents KuCoin keys / never enables Auto.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Row([string]$Name, [string]$State, [string]$Note = "") {
  "{0,-22} {1,-10} {2}" -f $Name, $State, $Note
}

Write-Host "DUP Market Radar ops_board (NO soak)"
Write-Host "Root: $Root"
Write-Host ""

$rows = @()

# radar up?
try {
  $live = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/live" -TimeoutSec 2
  if ($live.ok) { $rows += Row "radar_http" "UP" "127.0.0.1:8787" }
  else { $rows += Row "radar_http" "DOWN" "ok=false" }
} catch {
  $rows += Row "radar_http" "DOWN" "not listening"
}

# gate
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py "$Root\scripts\radar_gate.py" | Out-Null
if ($LASTEXITCODE -eq 0) { $rows += Row "radar_gate" "PASS" }
else { $rows += Row "radar_gate" "FAIL" "python scripts/radar_gate.py" }

# READY_OPS
$readyPath = Join-Path $Root "data\evidence\READY_OPS.json"
if (Test-Path $readyPath) {
  $ready = Get-Content $readyPath -Raw | ConvertFrom-Json
  if ($ready.ops_ready) { $rows += Row "ops_ready" "TRUE" "READY_OPS.json" }
  else { $rows += Row "ops_ready" "FALSE" "ops_ready!=true" }
} else {
  $rows += Row "ops_ready" "MISSING" "run go_ready.ps1"
}

# verify-full pack
$pack = Get-ChildItem (Join-Path $Root "docs\evidence\runs") -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match "verify-full" } |
  Sort-Object Name -Descending |
  Select-Object -First 1
if ($pack) { $rows += Row "verify_full_pack" "YES" $pack.Name }
else { $rows += Row "verify_full_pack" "NO" "package after verify Full" }

# 48h claim honesty
$rows += Row "soak_48h_claim" "NOT" "never without approx_48h_claim_ok pack"

# stale soak lock files
$stale = @()
foreach ($f in @("soak_48h_running.log", "soak_48h_running.err")) {
  $p = Join-Path $Root "data\evidence\$f"
  if (Test-Path $p) { $stale += $f }
}
if ($stale.Count) {
  $rows += Row "stale_soak_lock" "WARN" ($stale -join ",")
} else {
  $rows += Row "stale_soak_lock" "CLEAN" ""
}

# live-money blockers (presence only)
$envPath = Join-Path $Root ".env"
$missing = @()
if (-not (Test-Path $envPath)) {
  $missing += ".env"
} else {
  $map = @{}
  Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
    $p = $_ -split '=', 2
    $n = $p[0].Trim()
    $v = if ($p.Count -gt 1) { $p[1].Trim().Trim('"').Trim("'") } else { "" }
    if ($n) { $map[$n] = $v }
  }
  foreach ($k in @("KUCOIN_API_KEY","KUCOIN_API_SECRET","KUCOIN_API_PASSPHRASE","RADAR_API_KEY")) {
    if (-not $map.ContainsKey($k) -or [string]::IsNullOrWhiteSpace($map[$k])) { $missing += $k }
  }
}
if ($missing.Count) {
  $rows += Row "live_money_env" "BLOCKED" ($missing -join ",")
} else {
  $rows += Row "live_money_env" "KEYS_SET" "still need go_live_money PASS"
}

$rows | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "Next non-soak: fill KuCoin Spot keys then go_live_money.ps1"
Write-Host "Soak: only after explicit operator OK"
exit 0
