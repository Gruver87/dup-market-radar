# Root shim → scripts\build_native.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\build_native.ps1") @args
exit $LASTEXITCODE
