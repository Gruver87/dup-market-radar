# Root shim → scripts\build_ingest.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\build_ingest.ps1") @args
exit $LASTEXITCODE
