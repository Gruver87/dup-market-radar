# Shim from native/ → scripts\
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\scripts\build_native.ps1") @args
exit $LASTEXITCODE
