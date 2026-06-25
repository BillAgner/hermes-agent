# Kill stuck Python processes using >500MB RAM, then re-test
$targets = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.WorkingSet64 -gt 300MB }
foreach ($p in $targets) {
    Write-Host "Killing PID $($p.Id) ($([math]::Round($p.WorkingSet64/1MB,0))MB, started $($p.StartTime))"
    Stop-Process -Id $p.Id -Force
}
Start-Sleep 2
Write-Host "=== survivors >300MB ==="
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.WorkingSet64 -gt 300MB } | Select-Object Id, @{N='MB';E={[math]::Round($_.WorkingSet64/1MB,0)}}, StartTime
