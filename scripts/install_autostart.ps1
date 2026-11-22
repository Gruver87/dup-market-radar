# Register Windows Scheduled Task: start live-money radar at user logon.
# Requires: Interactive user session (AtLogon). Does not need admin for current-user task.
param(
  [string]$TaskName = "DUP-Market-Radar-Live",
  [string]$ProfileName = "live-money",
  [string]$HostAddr = "127.0.0.1",
  [int]$Port = 8787,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$starter = Join-Path $Root "scripts\start_radar_autostart.ps1"
if (-not (Test-Path $starter)) { throw "Missing $starter" }

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$starter`" -ProfileName `"$ProfileName`" -HostAddr `"$HostAddr`" -Port $Port"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Write-Host "Registered Scheduled Task: $TaskName (AtLogOn user=$env:USERNAME profile=$ProfileName :$Port)"

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "Started task now."
}

Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State, @{n="Triggers";e={$_.Triggers | ForEach-Object { $_.ToString() }}}
