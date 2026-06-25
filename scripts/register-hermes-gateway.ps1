<#
.SYNOPSIS
    Registers the Hermes_Gateway scheduled task.
.DESCRIPTION
    Run this ONCE from an elevated PowerShell to install or re-install the task.
    The task starts the Hermes Agent gateway (port 9119) at every boot.

    Includes restart-on-failure: up to 3 automatic restarts with 1-minute intervals
    if the gateway process crashes.

    Re-running this script is safe -- it overwrites the existing task.
#>

#Requires -RunAsAdministrator

$scriptPath  = "C:\Data\Hermes_0.17.0\gateway-service\Hermes_Gateway.cmd"
$taskName    = "Hermes_Gateway"
$runAsUser   = "ROC\bobup"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Registering $taskName" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute    "cmd.exe" `
    -Argument   "/c `"$scriptPath`"" `
    -WorkingDirectory "C:\Data\Hermes_0.17.0"

$trigger        = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay  = "PT1M"

$principal = New-ScheduledTaskPrincipal `
    -UserId    $runAsUser `
    -LogonType Interactive `
    -RunLevel  Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     (New-TimeSpan -Minutes 1)

try {
    Register-ScheduledTask `
        -TaskName   $taskName `
        -Action     $action `
        -Trigger    $trigger `
        -Principal  $principal `
        -Settings   $settings `
        -Description "Starts the Hermes Agent gateway (port 9119) at boot. Auto-restarts up to 3 times on crash." `
        -Force `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "  [FAIL] Register-ScheduledTask threw: $_" -ForegroundColor Red
    exit 1
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "  [FAIL] Task was not found after registration attempt." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  [OK] $taskName registered successfully." -ForegroundColor Green
Write-Host ""
Write-Host "  Principal    : $($task.Principal.UserId)  (LogonType=$($task.Principal.LogonType))" -ForegroundColor Gray
Write-Host "  Trigger      : AtStartup + 1min delay" -ForegroundColor Gray
Write-Host "  Restart      : up to 3 times, 1-minute interval" -ForegroundColor Gray
Write-Host "  Script       : $scriptPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  To run it manually now:" -ForegroundColor White
Write-Host "    cmd.exe /c `"$scriptPath`"" -ForegroundColor Gray
Write-Host ""
