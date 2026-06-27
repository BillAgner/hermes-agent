$ErrorActionPreference = 'Continue'

$dir = 'C:\Users\bobup\AppData\Local\Temp\DiagOutputDir\RdClientAutoTrace'

Write-Host "=== BEFORE ==="
Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue | Format-Table Name, LastWriteTime, Length -AutoSize
$before = (Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Count: $before"

# Run the task
$started = Start-ScheduledTask -TaskName 'CleanupRdClientAutoTrace'
Write-Host "Start result: $started"
Start-Sleep -Seconds 5

Write-Host "=== AFTER ==="
Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue | Format-Table Name, LastWriteTime, Length -AutoSize
$after = (Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Count: $after"

$info = Get-ScheduledTaskInfo -TaskName 'CleanupRdClientAutoTrace'
Write-Host "LastRunTime: $($info.LastRunTime)"
Write-Host "LastTaskResult code: $($info.LastTaskResult) (0 = success, 1 = success, 267011=still running, 267014=timeout)"

# Also check who owns the files - SYSTEM may not have delete rights
Write-Host "=== FILE OWNERSHIP (first file) ==="
$firstFile = Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue | Select-Object -First 1
if ($firstFile) {
    $acl = Get-Acl $firstFile.FullName
    Write-Host "Owner: $($acl.Owner)"
    Write-Host "Full path: $($firstFile.FullName)"
}

# Check task definition XML for reference
Write-Host "=== TASK XML (Action) ==="
$task = Get-ScheduledTask -TaskName 'CleanupRdClientAutoTrace'
$task.Xml | Select-Xml -XPath "//Exec/CommandLine" | ForEach-Object { $_.Node.InnerText }
$task.Xml | Select-Xml -XPath "//Exec/Arguments" | ForEach-Object { $_.Node.InnerText }