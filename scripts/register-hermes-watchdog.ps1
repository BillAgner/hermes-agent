<#
.SYNOPSIS
    Registers the Hermes_Watchdog scheduled task (0.17.0) and disables
    the stale Hermes_Dashboard task from the 0.16.0 installation.
.DESCRIPTION
    Run from an elevated PowerShell. Safe to re-run.
#>

#Requires -RunAsAdministrator

$ProjectRoot  = "C:\Data\Hermes_0.17.0"
$PythonW      = "$ProjectRoot\venv\Scripts\pythonw.exe"
$WatchdogScript = "$ProjectRoot\scripts\hermes_watchdog.py"

# ── 1. Disable the stale 0.16.0 Hermes_Dashboard task ──────────────────────
Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Disabling stale Hermes_Dashboard (0.16.0)" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
$oldDash = Get-ScheduledTask -TaskName "Hermes_Dashboard" -ErrorAction SilentlyContinue
if ($oldDash) {
    Disable-ScheduledTask -TaskName "Hermes_Dashboard" -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  [OK] Hermes_Dashboard disabled." -ForegroundColor Green
} else {
    Write-Host "  [--] Hermes_Dashboard not found, nothing to disable." -ForegroundColor Gray
}

# ── 2. Register Hermes_Watchdog pointing at 0.17.0 ─────────────────────────
Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Registering Hermes_Watchdog (0.17.0)" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$taskName  = "Hermes_Watchdog"
$runAsUser = "ROC\bobup"

$action = New-ScheduledTaskAction `
    -Execute          $PythonW `
    -Argument         "`"$WatchdogScript`" --daemon" `
    -WorkingDirectory $ProjectRoot

$trigger       = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT2M"   # 2-minute delay so gateway is up first

$principal = New-ScheduledTaskPrincipal `
    -UserId    $runAsUser `
    -LogonType Interactive `
    -RunLevel  Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0)   # no time limit (daemon)

try {
    Register-ScheduledTask `
        -TaskName   $taskName `
        -Action     $action `
        -Trigger    $trigger `
        -Principal  $principal `
        -Settings   $settings `
        -Description "Hermes Agent watchdog daemon (0.17.0). Probes gateway, MCPs, and Docker containers every 60s; auto-recovers failures." `
        -Force `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
    exit 1
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "  [FAIL] Task not found after registration." -ForegroundColor Red
    exit 1
}

Write-Host "  [OK] $taskName registered." -ForegroundColor Green
Write-Host "  Executable : $PythonW" -ForegroundColor Gray
Write-Host "  Script     : $WatchdogScript --daemon" -ForegroundColor Gray
Write-Host "  Trigger    : AtStartup + 2min delay" -ForegroundColor Gray
Write-Host ""

# ── 3. Start the watchdog daemon right now ───────────────────────────────────
Write-Host "  Starting watchdog daemon now..." -ForegroundColor White
Start-ScheduledTask -TaskName $taskName
Start-Sleep 3
$wdProc = Get-WmiObject Win32_Process | Where-Object {
    $_.CommandLine -like "*hermes_watchdog*" -and $_.Name -like "python*"
}
if ($wdProc) {
    Write-Host "  [OK] Watchdog running (PID $($wdProc.ProcessId))." -ForegroundColor Green
} else {
    Write-Host "  [WARN] Watchdog process not visible yet - may still be starting." -ForegroundColor Yellow
}
Write-Host ""
