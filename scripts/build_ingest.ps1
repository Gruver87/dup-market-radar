# Build release ingest binaries into native/target/release.
#   .\build_ingest.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
$native = Join-Path $Root "native"
Set-Location $native
Write-Host "repo: $Root"
Write-Host "== cargo build -p radar_ingest --release (bins) =="
# cargo progress on stderr — do not treat as terminating under Stop.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
cargo build -p radar_ingest --release --bins
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($code -ne 0) { exit $code }
$rel = Join-Path $native "target\release"
Get-ChildItem $rel -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like "radar-ingest-*" -and ($_.Extension -eq ".exe" -or $_.Extension -eq "") } |
  ForEach-Object { Write-Host ("built: " + $_.Name) }
exit 0
