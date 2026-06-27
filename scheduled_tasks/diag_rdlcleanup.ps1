$ErrorActionPreference = 'Continue'

# Check processes that might be holding the files open
Write-Host '=== MSRDC-RELATED PROCESSES ==='
Get-Process | Where-Object { $_.ProcessName -match 'msrdc|MSRDC|wppautotrace' } | Select-Object Name, Id, StartTime | Format-Table -AutoSize

# Confirm scheduled task registered correctly via raw schtasks
Write-Host ''
Write-Host '=== TASK STATE (raw schtasks) ==='
schtasks.exe //Query //TN 'CleanupRdClientAutoTrace' //V //FO LIST

Write-Host ''
Write-Host '=== SCHEDULED TASK NEXT/LAST RUN ==='
$info = Get-ScheduledTaskInfo -TaskName 'CleanupRdClientAutoTrace'
Write-Host "NextRunTime:    $($info.NextRunTime)"
Write-Host "LastRunTime:    $($info.LastRunTime)"
Write-Host "LastTaskResult: $($info.LastTaskResult) (0=success)"

# Now run the user's literal command and see what happens
Write-Host ''
Write-Host '=== RUNNING THE USERS LITERAL COMMAND ==='
$before = (Get-ChildItem -LiteralPath 'C:\Users\bobup\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace' -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Files before: $before"

try {
    Remove-Item 'C:\Users\bobup\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace\*' -Force -ErrorAction Stop
    Write-Host 'Remove-Item exited cleanly'
} catch {
    Write-Host "Remove-Item error: $($_.Exception.Message)"
}

$after = (Get-ChildItem -LiteralPath 'C:\Users\bobup\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace' -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Files after:  $after"