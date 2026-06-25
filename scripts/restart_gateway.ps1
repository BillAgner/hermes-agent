<#
.SYNOPSIS
    Restart the Hermes Gateway so the new rp_* endpoints + auto-load
    hook take effect. Self-verifying.
#>
$ErrorActionPreference = "Continue"
$ProjectRoot = "C:\Data\Hermes_0.17.0"

function Step($n, $msg) {
    Write-Host "[$n/7] $msg" -ForegroundColor Cyan
}

function Ok($msg) { Write-Host "  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  $msg" -ForegroundColor Red; exit 1 }

Step "1" "Stopping Hermes_Gateway scheduled task..."
try {
    Stop-ScheduledTask -TaskName "Hermes_Gateway" -ErrorAction Stop | Out-Null
    Ok "task stopped."
} catch { Warn "task stop: $_" }

Step "2" "Killing listener on port 9119..."
$listener = netstat -ano | Select-String "127.0.0.1:9119.*LISTENING"
if ($listener) {
    $listenerPid = ($listener -split '\s+')[-1] | Select-Object -First 1
    taskkill /F /PID $listenerPid 2>&1 | Out-Null
    Start-Sleep -Seconds 1
    Ok "killed listener PID $listenerPid."
} else {
    Warn "no listener (already gone)."
}

Step "3" "Killing supervisor from gateway.pid..."
$pidFile = Join-Path $ProjectRoot "gateway.pid"
if (Test-Path $pidFile) {
    $raw = Get-Content $pidFile -Raw
    $supervisorPid = $null
    if ($raw -match '"pid"\s*:\s*(\d+)') { $supervisorPid = [int]$Matches[1] }
    if ($supervisorPid) {
        $proc = Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $supervisorPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            Ok "killed supervisor PID $supervisorPid ($($proc.ProcessName))."
        } else { Warn "supervisor not running." }
    }
}

Step "4" "Waiting for port 9119 to clear..."
$cleared = $false
for ($i = 0; $i -lt 15; $i++) {
    $still = netstat -ano | Select-String "127.0.0.1:9119.*LISTENING"
    if (-not $still) { $cleared = $true; break }
    Start-Sleep -Seconds 1
}
if ($cleared) { Ok "port clear." } else { Warn "port still held after 15s." }

Step "5" "Starting Hermes_Gateway task..."
try {
    Start-ScheduledTask -TaskName "Hermes_Gateway" -ErrorAction Stop | Out-Null
    Ok "task started (gateway will come up in 5-30s)."
} catch { Fail "could not start task: $_" }

Step "6" "Polling http://127.0.0.1:9119/ ..."
$gatewayReady = $false
$token = $null
for ($i = 0; $i -lt 36; $i++) {
    try {
        $tmp = Join-Path $env:TEMP "hermes_dash_check.html"
        $code = (curl.exe -s -o $tmp -w "%{http_code}" http://127.0.0.1:9119/)
        if ($code -eq "200") {
            $body = Get-Content $tmp -Raw -ErrorAction SilentlyContinue
            # The token regex needs no escaping because we match the literal
            # HTML substring the SPA injects.
            $pattern = [regex]::Escape('__HERMES_SESSION_TOKEN__') + '="([^"]+)"'
            $m = [regex]::Match($body, $pattern)
            if ($m.Success) {
                $token = $m.Groups[1].Value
                $gatewayReady = $true
                break
            }
        }
    } catch {}
    Start-Sleep -Seconds 3
    if (($i % 5) -eq 4) { Write-Host "  ...still waiting ($($i*3)s)" -ForegroundColor Gray }
}
if (-not $gatewayReady) { Fail "gateway did not respond within 108s." }
Ok "gateway ready (token len $($token.Length))."

Step "7" "Probing new research-project endpoints..."
$hdr = @{ "X-Hermes-Session-Token" = $token }
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9119/api/research/projects" `
        -Headers $hdr -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $data = $r.Content | ConvertFrom-Json
    Ok ("/api/research/projects: HTTP 200, " + $data.count + " projects, storage=" + $data.storage_root)
} catch { Fail "/api/research/projects: $_" }
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9119/research-dashboard" `
        -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    Ok ("/research-dashboard: HTTP 200, " + $r.Content.Length + " bytes")
} catch { Fail "/research-dashboard: $_" }

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  Hermes Gateway restarted, research-project endpoints live" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
exit 0