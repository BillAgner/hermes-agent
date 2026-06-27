#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

$taskName = 'CleanupRdClientAutoTrace'
$targetDir = 'C:\Users\bobup\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace'

# Build the inner command that runs inside the scheduled task.
# Use -LiteralPath + wildcard to avoid issues with special chars, and swallow errors
# so the task itself never reports failure just because the dir was empty/missing.
$innerCmd = "Remove-Item -LiteralPath '$targetDir\*' -Force -Recurse -ErrorAction SilentlyContinue"

$action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$innerCmd`""

$trigger = New-ScheduledTaskTrigger -Daily -At '00:00'

# Run as SYSTEM so it doesn't require an interactive logon session to fire.
$principal = New-ScheduledTaskPrincipal `
  -UserId 'SYSTEM' `
  -LogonType 'ServiceAccount' `
  -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
  -MultipleInstances IgnoreNew `
  -Compatibility 'Win8'

Register-ScheduledTask `
  -TaskName $taskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description 'Daily cleanup of RdClientAutoTrace temp diagnostics directory' `
  -Force | Out-Null

Write-Host '=== TASK CREATED ==='
Get-ScheduledTask -TaskName $taskName | Format-List TaskName, State, Author

Write-Host '=== TRIGGER ==='
$task = Get-ScheduledTask -TaskName $taskName
$task.Triggers | Format-List Type, StartBoundary, DaysOfWeek, Enabled

Write-Host '=== ACTION ==='
$task.Actions | Format-List Execute, Arguments

Write-Host '=== PRINCIPAL ==='
$task.Principal | Format-List UserId, LogonType, RunLevel

Write-Host '=== NEXT RUN ==='
Get-ScheduledTaskInfo -TaskName $taskName | Format-List TaskName, NextRunTime, LastRunTime