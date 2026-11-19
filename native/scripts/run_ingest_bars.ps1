# native/scripts shim → repo scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\..\scripts\run_ingest_bars.ps1") @args
exit $LASTEXITCODE
