# Back-compat wrapper -> unified verify.ps1 -Profile Soak48h
# Prefer: .\scripts\verify.ps1 -Profile Soak48h
# PreSoak runs first by default. -SkipPreSoak only if gate already PASS.
param(
    [double]$IntervalSec = 300,
    [double]$Hours = 48,
    [int]$RetainKeep = 40,
    [switch]$SkipPreSoak,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "verify.ps1") `
    -Profile Soak48h `
    -Soak48Hours $Hours `
    -Soak48IntervalSec $IntervalSec `
    -RetainKeep $RetainKeep `
    -SkipPreSoak:$SkipPreSoak `
    -SkipBuild:$SkipBuild
exit $LASTEXITCODE
