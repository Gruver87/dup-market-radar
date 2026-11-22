# DUP Market Radar — durable start for Task Scheduler / reboot.
# Loads .env (no secret print), RADAR_PROFILE=live-money, single instance on :8787.
param(
  [string]$ProfileName = "live-money",
  [string]$HostAddr = "127.0.0.1",
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$logDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("autostart_{0:yyyyMMdd}.log" -f (Get-Date))

function Write-AutoLog([string]$msg) {
  $line = "{0:u} {1}" -f (Get-Date).ToUniversalTime(), $msg
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Host $line
}

# Stop duplicate servers (same script) — leave other python alone.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*run_radar_server.py*" } |
  ForEach-Object {
    Write-AutoLog "Stopping duplicate PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 2

# Load .env into process (presence only in log).
$envPath = Join-Path $Root ".env"
$keyPresent = $false
if (Test-Path $envPath) {
  Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $i = $line.IndexOf("=")
    if ($i -lt 1) { return }
    $k = $line.Substring(0, $i).Trim()
    $v = $line.Substring($i + 1).Trim()
    if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
      $v = $v.Substring(1, $v.Length - 2)
    }
    Set-Item -Path "Env:$k" -Value $v
    if ($k -eq "RADAR_API_KEY" -and $v) { $keyPresent = $true }
  }
  Write-AutoLog ".env loaded key_present=$keyPresent"
} else {
  Write-AutoLog "WARN: .env missing — KuCoin/API may fail closed"
}

$env:RADAR_PROFILE = $ProfileName
$env:PYTHONPATH = Join-Path $Root "src"
Write-AutoLog "RADAR_PROFILE=$env:RADAR_PROFILE host=$HostAddr port=$Port"

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-AutoLog "ERROR: venv python missing at $py"
  exit 1
}
$server = Join-Path $Root "scripts\run_radar_server.py"
if (-not (Test-Path $server)) {
  Write-AutoLog "ERROR: missing $server"
  exit 1
}

$proc = Start-Process -FilePath $py `
  -ArgumentList @($server, "--host", $HostAddr, "--port", "$Port") `
  -WorkingDirectory $Root `
  -WindowStyle Minimized `
  -PassThru
Write-AutoLog "Started PID=$($proc.Id)"
exit 0
