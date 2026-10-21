# Build radar_native (Rust/PyO3) into the active venv — same hybrid pattern as abs_native.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating .venv…"
  python -m venv .venv
  $py = Join-Path $Root ".venv\Scripts\python.exe"
  & $py -m pip install -q -r requirements.txt
}

& $py -m pip install -q "maturin>=1.7,<2.0"
Set-Location (Join-Path $Root "native\radar_native")
& $py -m maturin develop --release
Set-Location $Root
& $py -c "import radar_native; print('radar_native OK', radar_native.native_version())"
