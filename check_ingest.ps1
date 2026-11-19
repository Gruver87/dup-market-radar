# Root shim → scripts\check_ingest.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\check_ingest.ps1") @args
exit $LASTEXITCODE
