<#
.SYNOPSIS
    Registers the Hermes_Startup scheduled task.
.DESCRIPTION
    Run this ONCE from an elevated PowerShell to install the task.
    The task fires at every boot (AtStartup + 1min delay) and brings up
    the Docker containers Hermes needs: Honcho stack, Qdrant, Speaches.

    Mirrors the configuration of the existing Hermes_WSL_KeepAlive task:
      - Interactive logon as bobup (needed because Docker Desktop.exe requires
        a desktop session to initialize its WSL2 backend)
      - RunLevel Highest
      - AtStartup trigger with 1-minute delay

    Re-running this script is safe -- it overwrites the existing task.
#>

#Requires -RunAsAdministrator

$scriptPath  = "C:\Data\Hermes_0.17.0\scripts\startup_hermes.ps1"
$taskName    = "Hermes_Startup"
$runAsUser   = "ROC\bobup"   # same user as Hermes_Gateway

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Registering $taskName" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute    "powershell.exe" `
    -Argument   "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" `
    -WorkingDirectory "C:\Data\Hermes_0.17.0\scripts"

$trigger           = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay     = "PT1M"   # 1-minute delay -- gives Docker Desktop time to initialize

# S4U would be ideal but Docker Desktop.exe requires an interactive desktop
# session to initialize its WSL2 backend. Use Interactive logon (same as
# Hermes_WSL_KeepAlive) -- the task fires when the user is logged in.
$principal = New-ScheduledTaskPrincipal `
    -UserId    $runAsUser `
    -LogonType Interactive `
    -RunLevel  Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew `
    -ExecutionTimeLimit  (New-TimeSpan -Minutes 8)

try {
    Register-ScheduledTask `
        -TaskName   $taskName `
        -Action     $action `
        -Trigger    $trigger `
        -Principal  $principal `
        -Settings   $settings `
        -Description "Starts Honcho stack, Qdrant, and Speaches at boot for Hermes Agent." `
        -Force `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "  [FAIL] Register-ScheduledTask threw: $_" -ForegroundColor Red
    Write-Host "  Trying schtasks fallback..." -ForegroundColor Yellow

    $tr = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
    schtasks.exe /Create /TN $taskName /TR $tr /SC ONSTART /DELAY 0001:00 /RU $runAsUser /RL HIGHEST /F
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] schtasks fallback also failed. Exit code: $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
}

# Verify it actually landed
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "  [FAIL] Task was not found after registration attempt." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  [OK] $taskName registered successfully." -ForegroundColor Green
Write-Host ""
Write-Host "  Principal : $($task.Principal.UserId)  (LogonType=$($task.Principal.LogonType))" -ForegroundColor Gray
Write-Host "  Trigger   : AtStartup + 1min delay" -ForegroundColor Gray
Write-Host "  Script    : $scriptPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  To run it manually now (does not require admin):" -ForegroundColor White
Write-Host "    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -ForegroundColor Gray
Write-Host ""
