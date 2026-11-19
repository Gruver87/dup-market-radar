# Root shim → scripts\verify_native.ps1
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\verify_native.ps1") @args
exit $LASTEXITCODE
