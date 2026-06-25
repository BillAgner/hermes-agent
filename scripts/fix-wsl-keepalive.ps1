<#
.SYNOPSIS
    Harden hermes-tools WSL availability. Self-contained, self-verifying.

.DESCRIPTION
    The actual root cause: C:\Users\<user>\.wslconfig had a UTF-8 BOM that
    prevented WSL from parsing the file. The intended `vmIdleTimeout=-1`
    was silently ignored, so WSL defaulted to its 60-second idle timeout
    and shut down hermes-tools ~30s after the last wsl.exe session ended.

    This script:
      1. Strips the BOM from .wslconfig (preserving all existing settings).
      2. Verifies `vmIdleTimeout=-1` is present (and adds it if not).
      3. Restarts WSLService so the corrected .wslconfig is re-read.
      4. Verifies empirically that hermes-tools stays Running for 5 minutes
         with NO keep-alive ping.

    After this, hermes-tools stays Running indefinitely on its own. The
    watchdog cron (probe_wsl) is the safety net - if it ever becomes
    unreachable, the watchdog will boot it back up on its 10-min cycle.

    The old Hermes_WSL_KeepAlive scheduled task is also unregistered, since
    its auto-respawn doesn't work reliably with AtLogon triggers AND it's
    now redundant given vmIdleTimeout=-1.

.NOTES
    Requires elevation (admin). Re-launch via fix-wsl-keepalive.cmd if needed.
    Ends with [OK] or [FAIL] line.
#>

$ErrorActionPreference = 'Stop'

$distroName        = 'hermes-tools'
$wslConfigPath     = Join-Path $env:USERPROFILE '.wslconfig'
$taskName          = 'Hermes_WSL_KeepAlive'

function Step { param($m) Write-Host "[STEP] $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "[OK]   $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail { param($m) Write-Host "[FAIL] $m" -ForegroundColor Red ; exit 1 }
function Info { param($m) Write-Host "       $m" -ForegroundColor Gray  }

# ---------- 0. Pre-flight ----------
Step "Pre-flight"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Not running as Administrator. Re-launch via: cmd /c `"$PSCommandPath`"  (or use fix-wsl-keepalive.cmd)"
}

$wslStatus = & wsl.exe --status 2>&1
if ($LASTEXITCODE -ne 0) { Fail "wsl.exe not callable: $wslStatus" }

# wsl.exe emits UTF-16LE with NUL bytes; strip them for matching
$listOut = ((& wsl.exe -l -v 2>&1 | Out-String) -replace "`0", '')
if ($listOut -notmatch [regex]::Escape($distroName)) {
    Fail "Distro '$distroName' is not registered. Aborting."
}
Ok "wsl.exe OK; '$distroName' is registered; running as admin"

# ---------- 1. Patch .wslconfig: strip BOM, ensure vmIdleTimeout=-1 ----------
Step "Patch $wslConfigPath (strip BOM if present, ensure vmIdleTimeout=-1)"

if (-not (Test-Path $wslConfigPath)) {
    Fail "$wslConfigPath does not exist. Create it with at least [wsl2] vmIdleTimeout=-1"
}

$content = [System.IO.File]::ReadAllBytes($wslConfigPath)
$hadBom = $false
if ($content.Length -ge 3 -and $content[0] -eq 0xEF -and $content[1] -eq 0xBB -and $content[2] -eq 0xBF) {
    $content = $content[3..($content.Length - 1)]
    $hadBom = $true
    Info "stripped UTF-8 BOM (this was the root cause)"
}
# Normalize CRLF -> LF
$text = [System.Text.Encoding]::UTF8.GetString([byte[]]$content)
$text = $text -replace "`r`n", "`n" -replace "`r", "`n"

# Ensure vmIdleTimeout=-1 is in [wsl2]
if ($text -notmatch '(?m)^\s*vmIdleTimeout\s*=\s*-1\s*$') {
    if ($text -match '(?m)^\s*\[wsl2\]\s*$') {
        $text = $text -replace '(?m)(^\s*\[wsl2\]\s*$)', "`$1`nvmIdleTimeout=-1"
        Info "added vmIdleTimeout=-1 to existing [wsl2] section"
    } else {
        $text += "`n[wsl2]`nvmIdleTimeout=-1"
        Info "added [wsl2] section with vmIdleTimeout=-1"
    }
} else {
    Info "vmIdleTimeout=-1 already present"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($False)
[System.IO.File]::WriteAllText($wslConfigPath, $text, $utf8NoBom)
Ok "Wrote $wslConfigPath (BOM was=$hadBom, now=clean)"

# ---------- 2. Restart WSLService so .wslconfig is re-read ----------
Step "Restart WSLService (wsl --shutdown is NOT enough - the service caches the file)"
$svc = Get-Service -Name 'WSLService' -ErrorAction SilentlyContinue
if (-not $svc) { Fail "WSLService not found. Is WSL installed?" }
Restart-Service -Name 'WSLService' -Force
Start-Sleep -Seconds 5
$svc2 = Get-Service -Name 'WSLService'
if ($svc2.Status -ne 'Running') { Fail "WSLService not Running after restart: $($svc2.Status)" }
Ok "WSLService is Running"

# ---------- 3. Boot hermes-tools ----------
Step "Boot $distroName"
& wsl.exe -d $distroName -- bash -c 'cd / && /bin/true' 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Could not boot $distroName" }
Start-Sleep -Seconds 3
$stateNow = (((& wsl.exe -l -v 2>&1 | Out-String) -replace "`0", '') | Select-String $distroName) -replace '\s+', ' '
Info "state after boot: $stateNow.Trim()"

# ---------- 4. Remove the obsolete keep-alive scheduled task ----------
Step "Remove obsolete keep-alive task '$taskName' (now redundant with vmIdleTimeout=-1)"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false | Out-Null
    Ok "Unregistered '$taskName'"
} else {
    Info "Task not present (clean slate)"
}
# Kill any straggler keep-alive processes
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'keep-wsl-alive\.ps1' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
if ($killed -gt 0) { Info "killed $killed straggler keep-alive process(es)" }

# ---------- 5. THE HARDENING TEST: 5 minutes, no keep-alive, must stay Running ----------
Step "Hardening proof: 5 minutes with NO keep-alive; hermes-tools must stay Running"

$startTime = Get-Date
$probeResults = @()
foreach ($i in 1..5) {
    Start-Sleep -Seconds 60
    $uptimeRaw = & wsl.exe -d $distroName -- bash -c 'cd / && uptime' 2>&1
    $uptime = ($uptimeRaw -join "`n") -replace "`0", ''
    $uptime = ($uptime -split "`n" | Select-Object -First 1).Trim()
    $probeResults += "t=$([math]::Round(((Get-Date) - $startTime).TotalSeconds, 0))s : $uptime"
    Info $probeResults[-1]
}

# Final active probe to confirm it's reachable
$probeRaw = & wsl.exe -d $distroName -- bash -c 'cd / && echo PROBE_OK' 2>&1
$finalProbe = (($probeRaw -join "`n") -replace "`0", '').Trim()
if ($finalProbe -notmatch 'PROBE_OK') {
    Fail "Final active probe failed: '$finalProbe' (vmIdleTimeout=-1 not working)"
}

# Uptime should have grown ~5 minutes from boot
$uptimeRaw2 = & wsl.exe -d $distroName -- bash -c 'cd / && cat /proc/uptime' 2>&1
$uptimeStr = (($uptimeRaw2 -join "`n") -replace "`0", '').Trim()
$uptimeSec = [double]($uptimeStr.Split()[0])
if ($uptimeSec -lt 240) {
    Fail "hermes-tools uptime is only ${uptimeSec}s after 5 min wait - VM was restarted during the test (vmIdleTimeout=-1 not honored)"
}
Ok "hermes-tools survived 5 minutes with no keep-alive (uptime: $uptimeStr)"

# ---------- Done ----------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "[OK] hermes-tools is now self-sustaining."                     -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Root cause: UTF-8 BOM in .wslconfig was preventing WSL from"   -ForegroundColor Gray
Write-Host "              parsing vmIdleTimeout=-1. Default 60s idle"        -ForegroundColor Gray
Write-Host "              timeout was shutting down hermes-tools ~30s after" -ForegroundColor Gray
Write-Host "              the last wsl.exe session ended."                    -ForegroundColor Gray
Write-Host ""                                                              -ForegroundColor Gray
Write-Host "  Fix applied:"                                                  -ForegroundColor Gray
Write-Host "   - Stripped BOM from $wslConfigPath"                          -ForegroundColor Gray
Write-Host "   - Restarted WSLService so the corrected file is re-read"      -ForegroundColor Gray
Write-Host "   - Unregistered obsolete Hermes_WSL_KeepAlive scheduled task"  -ForegroundColor Gray
Write-Host ""                                                              -ForegroundColor Gray
Write-Host "  Safety net:"                                                   -ForegroundColor Gray
Write-Host "   - watchdog.py probe_wsl pings hermes-tools every 10 min and"  -ForegroundColor Gray
Write-Host "     will boot it if it ever becomes unreachable."               -ForegroundColor Gray
exit 0