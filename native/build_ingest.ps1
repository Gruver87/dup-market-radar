# Shim from native/ → scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\scripts\build_ingest.ps1") @args
exit $LASTEXITCODE
