<#
.SYNOPSIS
    One-time setup: registers ALL Hermes scheduled tasks.
.DESCRIPTION
    Run this ONCE from an elevated (Run as Administrator) PowerShell to install
    or reinstall all Hermes scheduled tasks:

      Hermes_Startup   -- boots Docker stack + WSL at every logon (1-min delay)
      Hermes_Gateway   -- starts the messaging gateway at every logon, with
                          restart-on-failure (3 retries, 1-min interval)
      Hermes_Dashboard -- starts the web UI server (port 9119) at every logon,
                          with restart-on-failure (3 retries, 1-min interval)

    Re-running is safe -- overwrites existing tasks.

    To run elevated:
      Right-click PowerShell -> Run as Administrator, then:
      powershell -ExecutionPolicy Bypass -File "C:\Data\Hermes_0.17.0\scripts\setup-hermes-tasks.ps1"
#>

#Requires -RunAsAdministrator

$RunAsUser = $env:USERNAME  # current user (bobup)
$ProjectRoot = "C:\Data\Hermes_0.17.0"

function Register-HermesTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string]$Description,
        [int]$DelayMinutes = 1,
        [int]$RestartCount = 0,
        [string]$RestartInterval = "00:00:00"
    )

    $action = New-ScheduledTaskAction `
        -Execute    "powershell.exe" `
        -Argument   "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`"" `
        -WorkingDirectory $ProjectRoot

    $trigger = New-ScheduledTaskTrigger -AtLogon
    $trigger.Delay = "PT${DelayMinutes}M"

    $principal = New-ScheduledTaskPrincipal `
        -UserId    $RunAsUser `
        -LogonType Interactive `
        -RunLevel  Limited

    $settingsParams = @{
        StartWhenAvailable = $true
        MultipleInstances  = "IgnoreNew"
        ExecutionTimeLimit = (New-TimeSpan -Hours 0)
    }
    if ($RestartCount -gt 0) {
        $settingsParams['RestartCount']    = $RestartCount
        $settingsParams['RestartInterval'] = [timespan]$RestartInterval
    }
    $settings = New-ScheduledTaskSettingsSet @settingsParams

    try {
        Register-ScheduledTask `
            -TaskName   $TaskName `
            -Action     $action `
            -Trigger    $trigger `
            -Principal  $principal `
            -Settings   $settings `
            -Description $Description `
            -Force `
            -ErrorAction Stop | Out-Null
        Write-Host "  [OK] $TaskName" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  [FAIL] $TaskName : $_" -ForegroundColor Red
        return $false
    }
}

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Hermes -- Registering Scheduled Tasks" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Running as: $RunAsUser" -ForegroundColor Gray
Write-Host ""

# Hermes_Startup: boots Docker stack + WSL (at logon + 1 min delay)
Register-HermesTask `
    -TaskName   "Hermes_Startup" `
    -ScriptPath "$ProjectRoot\scripts\startup_hermes.ps1" `
    -Description "Starts Honcho Docker stack, Qdrant, Speaches, and WSL hermes-tools at logon." `
    -DelayMinutes 1 | Out-Null

# Hermes_Gateway: starts gateway with restart-on-failure (at logon + 1 min delay)
$gtwAction = New-ScheduledTaskAction `
    -Execute    "cmd.exe" `
    -Argument   "/c `"$ProjectRoot\gateway-service\Hermes_Gateway.cmd`"" `
    -WorkingDirectory $ProjectRoot

$gtwTrigger = New-ScheduledTaskTrigger -AtLogon
$gtwTrigger.Delay = "PT1M"

$gtwPrincipal = New-ScheduledTaskPrincipal `
    -UserId    $RunAsUser `
    -LogonType Interactive `
    -RunLevel  Limited

$gtwSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     ([timespan]"00:01:00")

try {
    Register-ScheduledTask `
        -TaskName   "Hermes_Gateway" `
        -Action     $gtwAction `
        -Trigger    $gtwTrigger `
        -Principal  $gtwPrincipal `
        -Settings   $gtwSettings `
        -Description "Starts Hermes Agent gateway (port 9119) at logon. Auto-restarts up to 3x on crash." `
        -Force `
        -ErrorAction Stop | Out-Null
    Write-Host "  [OK] Hermes_Gateway (with restart-on-failure)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Hermes_Gateway : $_" -ForegroundColor Red
}

# Hermes_Dashboard: starts web UI server on port 9119 (at logon + 2 min delay)
$dashAction = New-ScheduledTaskAction `
    -Execute    "cmd.exe" `
    -Argument   "/c `"$ProjectRoot\gateway-service\Hermes_Dashboard.cmd`"" `
    -WorkingDirectory $ProjectRoot

$dashTrigger = New-ScheduledTaskTrigger -AtLogon
$dashTrigger.Delay = "PT2M"

$dashPrincipal = New-ScheduledTaskPrincipal `
    -UserId    $RunAsUser `
    -LogonType Interactive `
    -RunLevel  Limited

$dashSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     ([timespan]"00:01:00")

try {
    Register-ScheduledTask `
        -TaskName   "Hermes_Dashboard" `
        -Action     $dashAction `
        -Trigger    $dashTrigger `
        -Principal  $dashPrincipal `
        -Settings   $dashSettings `
        -Description "Starts Hermes web UI server (port 9119) at logon. Auto-restarts up to 3x on crash." `
        -Force `
        -ErrorAction Stop | Out-Null
    Write-Host "  [OK] Hermes_Dashboard (with restart-on-failure)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Hermes_Dashboard : $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "  Summary:" -ForegroundColor White
Get-ScheduledTask | Where-Object { $_.TaskName -like "Hermes*" } |
    Select-Object TaskName, State, @{N="RestartCount";E={$_.Settings.RestartCount}} |
    Format-Table -AutoSize

Write-Host ""
Write-Host "  Tasks will run at next logon." -ForegroundColor Gray
Write-Host "  To test manually:" -ForegroundColor White
Write-Host "    powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\startup_hermes.ps1`"" -ForegroundColor Gray
Write-Host "    cmd /c `"$ProjectRoot\gateway-service\Hermes_Gateway.cmd`"" -ForegroundColor Gray
Write-Host "    cmd /c `"$ProjectRoot\gateway-service\Hermes_Dashboard.cmd`"" -ForegroundColor Gray
Write-Host ""
