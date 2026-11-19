# Build + smoke Binance/Bybit WS ingest. Requires network + rustc.
# Works from repo root OR native/:
#   .\check_ingest.ps1
#   .\scripts\check_ingest.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
Set-Location $Root

$native = Join-Path $Root "native"
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  Write-Error "cargo not found — install Rust toolchain"
  exit 2
}

Write-Host "repo: $Root"
Write-Host "== cargo test radar_core / radar_ingest =="
Set-Location $native
cargo test -p radar_core -p radar_ingest -- --nocapture
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== radar-ingest-binance --seconds 4 =="
cargo run -p radar_ingest --release --bin radar-ingest-binance -- --seconds 4 --max-ticks 20
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== radar-ingest-bybit --seconds 4 =="
cargo run -p radar_ingest --release --bin radar-ingest-bybit -- --seconds 4 --max-ticks 20
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# KuCoin needs bullet-public HTTP + WS subscribe — give more than Binance/Bybit.
Write-Host "== radar-ingest-kucoin --seconds 10 =="
cargo run -p radar_ingest --release --bin radar-ingest-kucoin -- --seconds 10 --max-ticks 20
exit $LASTEXITCODE

