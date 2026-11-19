# Shim from native/ → scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\scripts\verify_native.ps1") @args
exit $LASTEXITCODE
