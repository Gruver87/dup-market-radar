# Root shim → scripts\run_ingest_binance.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\run_ingest_binance.ps1") @args
exit $LASTEXITCODE
