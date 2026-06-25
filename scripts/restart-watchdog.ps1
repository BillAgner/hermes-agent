#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Kill all running hermes_watchdog instances and restart the scheduled task.
.DESCRIPTION
    Run this from an elevated PowerShell whenever the watchdog is stuck.
    Safe to re-run.
#>

Write-Host "=== Hermes Watchdog Restart ===" -ForegroundColor Cyan
Write-Host ""

# 1. Kill all watchdog processes (any version, any Python)
$killed = 0
Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*hermes_watchdog*" } | ForEach-Object {
    Write-Host "  Killing PID=$($_.ProcessId)  $($_.Name)" -ForegroundColor Yellow
    try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        $killed++
    } catch {
        # taskkill as fallback
        taskkill /F /PID $_.ProcessId 2>$null | Out-Null
        $killed++
    }
}
if ($killed -eq 0) { Write-Host "  No watchdog processes found." -ForegroundColor Gray }
else { Write-Host "  Killed $killed process(es)." -ForegroundColor Green }

Start-Sleep 2

# 2. Restart via scheduled task
Write-Host ""
Write-Host "  Starting Hermes_Watchdog task..." -ForegroundColor White
try {
    Start-ScheduledTask -TaskName "Hermes_Watchdog" -ErrorAction Stop
    Start-Sleep 5
    $wd = Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*hermes_watchdog*" }
    if ($wd) {
        Write-Host "  [OK] Watchdog running (PID $($wd.ProcessId))" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Watchdog process not visible yet (may still be starting)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [FAIL] Could not start scheduled task: $_" -ForegroundColor Red
}
Write-Host ""
