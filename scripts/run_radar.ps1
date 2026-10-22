# DUP Market Radar - ops console
# Does NOT rebuild radar_native (use .\scripts\build_native.ps1).
# Default: no uvicorn --reload (stable). Dev hot-reload: -Reload (src/config/dashboard only).
param(
  [switch]$Reload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Pip/maturin leftover dirs under site-packages (~adar_native) - remove so they
# cannot confuse imports or (with broad --reload) trigger watch cascades.
$site = Join-Path $Root ".venv\Lib\site-packages"
if (Test-Path $site) {
  Get-ChildItem $site -Directory -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -like '~*adar*' -or
      $_.Name -like '~*radar_native*' -or
      $_.Name -eq '~adar_native'
    } |
    ForEach-Object {
      Write-Host ("Removing leftover: {0}" -f $_.Name)
      Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
    }
}

$env:PYTHONPATH = Join-Path $Root "src"
Write-Host "DUP Market Radar -> http://127.0.0.1:8787/"
Write-Host "Watchlist + manual/auto KuCoin. Futures never auto-traded."
Write-Host "Native: not rebuilt here (build_native.ps1 if needed)."

$uvArgs = @(
  "-m", "uvicorn",
  "radar.api.app:app",
  "--host", "127.0.0.1",
  "--port", "8787",
  "--limit-concurrency", "128",
  "--timeout-keep-alive", "30"
)

if ($Reload) {
  # Watch only app sources - never .venv / site-packages / cargo target.
  # Broad --reload was creating reload cascades + ~adar_native when .pyd locked.
  Write-Host "Reload: ON (dirs src, config, dashboard - .venv excluded)"
  $uvArgs += @(
    "--reload",
    "--reload-dir", (Join-Path $Root "src"),
    "--reload-dir", (Join-Path $Root "config"),
    "--reload-dir", (Join-Path $Root "dashboard"),
    "--reload-exclude", ".venv",
    "--reload-exclude", "**/site-packages/**",
    "--reload-exclude", "**/~*",
    "--reload-exclude", "**/native/**/target/**",
    "--reload-exclude", "**/evidence/**",
    "--reload-exclude", "**/.pytest_cache/**"
  )
} else {
  Write-Host "Reload: OFF (stable ops / PreSoak). Dev: .\scripts\run_radar.ps1 -Reload"
}

& $py @uvArgs
