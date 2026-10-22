# Build radar_native (Rust/PyO3) into the active venv.
# Uses maturin build + pip install (same as ci-native) to avoid
# "pip could not overwrite ... in use" / leftover ~adar_native on Windows.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating .venv..."
  python -m venv .venv
  $py = Join-Path $Root ".venv\Scripts\python.exe"
  & $py -m pip install -q -r requirements.txt
}

function Clear-RadarNativeLeftovers {
  $site = Join-Path $Root ".venv\Lib\site-packages"
  if (-not (Test-Path $site)) { return }
  Get-ChildItem $site -Directory -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -like '~*adar*' -or
      $_.Name -like '~*radar_native*' -or
      $_.Name -eq '~adar_native'
    } |
    ForEach-Object {
      Write-Host ("Removing leftover: {0}" -f $_.FullName)
      Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
    }
}

Clear-RadarNativeLeftovers

# pip/maturin write progress to stderr; do not treat as terminating under Stop.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $py -m pip install -q "maturin>=1.7,<2.0"
if ($LASTEXITCODE -ne 0) {
  $ErrorActionPreference = $prevEap
  Write-Error "pip install maturin failed (exit $LASTEXITCODE)"
  exit $LASTEXITCODE
}
$nativeDir = Join-Path $Root "native\radar_native"
$dist = Join-Path $nativeDir "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Set-Location $nativeDir
& $py -m maturin build --release -o dist
$buildCode = $LASTEXITCODE
if ($buildCode -ne 0) {
  Set-Location $Root
  $ErrorActionPreference = $prevEap
  exit $buildCode
}
$wheel = Get-ChildItem (Join-Path $dist "radar_native-*.whl") |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $wheel) {
  Set-Location $Root
  $ErrorActionPreference = $prevEap
  Write-Error "maturin build produced no wheel under native/radar_native/dist"
  exit 1
}
# Prefer install over develop: works when .pyd is locked by a running interpreter.
# Discard pip upgrade-notice noise on stderr so PS Stop does not false-fail.
& $py -m pip install --force-reinstall --no-deps $wheel.FullName 2>$null
$pipCode = $LASTEXITCODE
Set-Location $Root
Clear-RadarNativeLeftovers
$ErrorActionPreference = $prevEap
if ($pipCode -ne 0) {
  Write-Host "pip install failed (module may be locked). Stop other python/radar processes and retry."
  exit $pipCode
}
& $py -c "import radar_native; print('radar_native OK', radar_native.native_version())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
