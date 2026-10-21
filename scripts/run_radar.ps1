# DUP Market Radar — ops console
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$env:PYTHONPATH = Join-Path $Root "src"
Write-Host "DUP Market Radar → http://127.0.0.1:8787/"
Write-Host "Watchlist + manual/auto KuCoin. Futures never auto-traded."

# Bind loopback only. Concurrency soft-cap for hung-client flood.
& $py -m uvicorn radar.api.app:app --host 127.0.0.1 --port 8787 --reload `
  --limit-concurrency 128 --timeout-keep-alive 30
