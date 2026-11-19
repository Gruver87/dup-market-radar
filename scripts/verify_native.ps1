# One-shot native engine verify (Rust smoke + Python unit tests). No soak.
#   .\verify_native.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
Set-Location $Root
Write-Host "repo: $Root"

Write-Host "== check_ingest (WS + bars) =="
& (Join-Path $Root "scripts\check_ingest.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# check_ingest leaves cwd in native/ — always return to repo root for pytest
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$tests = @(
  (Join-Path $Root "tests\test_bar_agg.py"),
  (Join-Path $Root "tests\test_tick_ring.py"),
  (Join-Path $Root "tests\test_ws_annotate.py"),
  (Join-Path $Root "tests\test_ws_price_apply.py"),
  (Join-Path $Root "tests\test_ws_bar_stamp.py"),
  (Join-Path $Root "tests\test_short_window.py")
)
foreach ($t in $tests) {
  if (-not (Test-Path $t)) {
    Write-Error "missing test file: $t"
    exit 2
  }
}

Write-Host "== pytest native WS/bar tests =="
& $py -m pytest @tests -q --rootdir $Root
exit $LASTEXITCODE
