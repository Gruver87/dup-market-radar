# Remove DUP Market Radar Windows Scheduled Task.
param([string]$TaskName = "DUP-Market-Radar-Live")

$ErrorActionPreference = "Stop"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
  Write-Host "No task named $TaskName"
  exit 0
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered: $TaskName"
