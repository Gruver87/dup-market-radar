# Shim from native/ → scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\scripts\run_ingest_bybit.ps1") @args
exit $LASTEXITCODE
