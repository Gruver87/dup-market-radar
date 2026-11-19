# Shim from native/ → scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\scripts\check_ingest.ps1") @args
exit $LASTEXITCODE
