# Build + smoke Binance/Bybit/KuCoin/MEXC WS + bars. Requires network + rustc.
#   .\check_ingest.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
Set-Location $Root

$native = Join-Path $Root "native"
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  Write-Error "cargo not found — install Rust toolchain"
  exit 2
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"

Write-Host "repo: $Root"
Write-Host "== cargo test radar_core / radar_ingest =="
Set-Location $native
cargo test -p radar_core -p radar_ingest --lib -- --nocapture
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEap; exit $LASTEXITCODE }

Write-Host "== radar-ingest-binance --seconds 4 =="
cargo run -p radar_ingest --release --bin radar-ingest-binance -- --seconds 4 --max-ticks 20
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEap; exit $LASTEXITCODE }

Write-Host "== radar-ingest-bybit --seconds 4 =="
cargo run -p radar_ingest --release --bin radar-ingest-bybit -- --seconds 4 --max-ticks 20
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEap; exit $LASTEXITCODE }

Write-Host "== radar-ingest-kucoin --seconds 10 =="
cargo run -p radar_ingest --release --bin radar-ingest-kucoin -- --seconds 10 --max-ticks 20
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEap; exit $LASTEXITCODE }

Write-Host "== radar-ingest-mexc --seconds 8 =="
cargo run -p radar_ingest --release --bin radar-ingest-mexc -- --seconds 8 --max-ticks 20
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEap; exit $LASTEXITCODE }

Write-Host "== radar-ingest-bars --seconds 12 --interval-sec 5 =="
cargo run -p radar_ingest --release --bin radar-ingest-bars -- --seconds 12 --interval-sec 5 --symbols BTCUSDT,ETHUSDT
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEap
Set-Location $Root
exit $code
