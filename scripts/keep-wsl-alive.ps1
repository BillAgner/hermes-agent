# Hermes WSL keep-alive service.
# Pings hermes-tools distro every 20s to prevent WSL2 idle shutdown.
#
# Why this version doesn't leak handles: the previous version did
#   & wsl.exe -d hermes-tools -- /bin/true 2>&1
# inside a while(true) loop. Each iteration created child stdio pipes that
# PowerShell only released when the host collected them â€” under
# UseUnifiedSchedulingEngine this caused the process to die after a few
# hours. We now use Start-Process with redirected streams and explicit
# Dispose() so each iteration is fully clean.
$ErrorActionPreference = 'Continue'
$distroName     = 'hermes-tools'
$pingIntervalSec = 20
$logFile        = 'C:\Data\Hermes_0.17.0\logs\wsl-keep-alive.log'

$logDir = Split-Path $logFile -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Log-Message {
    param([string]$Message)
    $timestamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ'
    try { Add-Content -Path $logFile -Value "[$timestamp] $Message" } catch { }
}

Log-Message "WSL keep-alive starting (distro=$distroName, interval=${pingIntervalSec}s, pid=$PID)"

try {
    $null = & wsl.exe --status 2>&1
    Log-Message "wsl --status ok"
} catch {
    Log-Message "FATAL: wsl.exe not callable: $_"
    exit 1
}

$pingCount = 0
while ($true) {
    $pingCount++
    $p = $null
    try {
        $p = Start-Process -FilePath 'wsl.exe' `
            -ArgumentList @('-d', $distroName, '--', '/bin/true') `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput 'NUL' `
            -RedirectStandardError  'NUL'
        if (-not $p.WaitForExit(5000)) {
            try { $p.Kill() } catch { }
            Log-Message "ping #$pingCount timeout (>5s), killed"
        } else {
            $rc = $p.ExitCode
            if ($pingCount % 30 -eq 1) {
                Log-Message "ping #$pingCount rc=$rc"
            }
        }
    } catch {
        Log-Message "ping exception: $_"
    } finally {
        if ($p) { try { $p.Dispose() } catch { } }
    }
    Start-Sleep -Seconds $pingIntervalSec
}