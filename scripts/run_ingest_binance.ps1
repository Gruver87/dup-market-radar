# Live Binance trade ingest → NDJSON (watch only).
#   .\run_ingest_binance.ps1 8
#   .\scripts\run_ingest_binance.ps1 8
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_radar_root.ps1")
$Root = Get-RadarRepoRoot -StartPath $PSScriptRoot
$seconds = if ($args.Count -ge 1) { [int]$args[0] } else { 8 }
$symbols = if ($args.Count -ge 2) { [string]$args[1] } else { "BTCUSDT,ETHUSDT,SOLUSDT" }

Write-Host "repo: $Root"
Set-Location (Join-Path $Root "native")
cargo run -p radar_ingest --release --bin radar-ingest-binance -- --seconds $seconds --symbols $symbols
exit $LASTEXITCODE
