# Back-compat wrapper -> unified verify.ps1 -Profile PreSoak
# Prefer: .\scripts\verify.ps1 -Profile PreSoak
param(
    [switch]$SkipBuild,
    [int]$SoakCycles = 3,
    [double]$SoakIntervalSec = 45
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "verify.ps1") `
    -Profile PreSoak `
    -SkipBuild:$SkipBuild `
    -SoakCycles $SoakCycles `
    -SoakIntervalSec $SoakIntervalSec
exit $LASTEXITCODE
