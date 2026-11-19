# Shared: find dup-market-radar repo root from any caller path / cwd.
function Get-RadarRepoRoot {
  param([string]$StartPath = $null)
  $candidates = @()
  if ($StartPath) { $candidates += (Resolve-Path -LiteralPath $StartPath).Path }
  if ($PSScriptRoot) { $candidates += $PSScriptRoot }
  if ($MyInvocation.PSScriptRoot) { $candidates += $MyInvocation.PSScriptRoot }
  $candidates += (Get-Location).Path

  foreach ($start in $candidates) {
    $cur = $start
    for ($i = 0; $i -lt 8; $i++) {
      if (Test-Path (Join-Path $cur "native\Cargo.toml")) {
        return $cur
      }
      $parent = Split-Path -Parent $cur
      if (-not $parent -or $parent -eq $cur) { break }
      $cur = $parent
    }
  }
  throw "dup-market-radar root not found (need native\Cargo.toml). cd to the repo and retry."
}
