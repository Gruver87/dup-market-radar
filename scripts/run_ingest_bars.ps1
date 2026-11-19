# Live Binance trades → OHLCV bars (watch only).
#   .\run_ingest_bars.ps1 12 5
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
$seconds = if ($args.Count -ge 1) { [int]$args[0] } else { 12 }
$interval = if ($args.Count -ge 2) { [int]$args[1] } else { 5 }
$symbols = if ($args.Count -ge 3) { [string]$args[2] } else { "BTCUSDT,ETHUSDT" }

Write-Host "repo: $Root"
Set-Location (Join-Path $Root "native")
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
cargo run -p radar_ingest --release --bin radar-ingest-bars -- --seconds $seconds --interval-sec $interval --symbols $symbols
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEap
exit $code
