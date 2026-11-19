# Root shim → scripts\run_ingest_kucoin.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\run_ingest_kucoin.ps1") @args
exit $LASTEXITCODE
