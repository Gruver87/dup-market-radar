# native/scripts shim → repo scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\..\scripts\run_ingest_kucoin.ps1") @args
exit $LASTEXITCODE
