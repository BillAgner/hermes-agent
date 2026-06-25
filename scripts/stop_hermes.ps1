<#
.SYNOPSIS
    Hermes Complete Stack Stop
.DESCRIPTION
    Stops all Hermes components in reverse startup order:
      1. Hermes gateway process (pythonw.exe on port 9119)
      2. Open Notebook  (docker compose down at C:\Data\Hermes\~\open-notebook-local)
      3. Honcho stack   (docker compose down at C:\honcho)
      4. Qdrant         (standalone container)
      5. Speaches       (standalone container)
      6. WSL hermes-tools (optional -- pass -TerminateWSL to stop it)

    Safe to run manually at any time. After stopping you can re-start with:
      powershell -File "C:\Data\Hermes_0.17.0\scripts\startup_hermes.ps1"

    Use -TerminateWSL to also terminate the hermes-tools WSL distro.
    Use -StopDocker    to also stop Docker Desktop entirely (rarely needed).
#>

param(
    [switch]$TerminateWSL,
    [switch]$StopDocker
)

$ErrorActionPreference = "Continue"
$ProjectRoot = "C:\Data\Hermes_0.17.0"

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("stop_hermes_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
Start-Transcript -Path $LogFile -Append

$DockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ((Test-Path $DockerBin) -and ($env:PATH -notlike "*$DockerBin*")) {
    $env:PATH = "$DockerBin;$env:PATH"
}

. "$PSScriptRoot\_common.ps1"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Yellow
Write-Host "  Hermes -- Stopping All Components" -ForegroundColor Yellow
Write-Host "=======================================================" -ForegroundColor Yellow
Write-Host ""

# --- 1. Stop Hermes Gateway ---
Write-Host "[1/5] Hermes Gateway (port 9119)" -ForegroundColor White
$pidFile = Join-Path $ProjectRoot "gateway.pid"
$gatewayStopped = $false

# Step 1: Graceful stop via API (preferred — handles drain timeout)
$portListening = netstat -ano | Select-String "127.0.0.1:9119.*LISTENING"
if ($portListening) {
    Write-Host "  Sending graceful stop via API..." -ForegroundColor Cyan
    # Get session token from dashboard page
    try {
        $page = (Invoke-WebRequest -Uri "http://127.0.0.1:9119/" -UseBasicParsing -TimeoutSec 3).Content
        if ($page -match '__HERMES_SESSION_TOKEN__="([^"]+)"') {
            $apiToken = $Matches[1]
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:9119/api/gateway/stop" -Method POST `
                -Headers @{ "Authorization" = "Bearer $apiToken" } `
                -UseBasicParsing -TimeoutSec 10 -ErrorAction SilentlyContinue
            Write-Host "  API stop: $($r.Content)" -ForegroundColor Gray
            Start-Sleep -Seconds 8  # allow graceful drain
        }
    } catch { Write-Host "  API stop unavailable: $_" -ForegroundColor Gray }
}

# Step 2: Kill PID from gateway.pid if still running
if (Test-Path $pidFile) {
    $pidContent = Get-Content $pidFile -Raw -ErrorAction SilentlyContinue
    $gatewayPid = $null
    if ($pidContent -match '"pid"\s*:\s*(\d+)') { $gatewayPid = [int]$Matches[1] }
    elseif ($pidContent -match '^\d+$') { $gatewayPid = [int]$pidContent.Trim() }
    if ($gatewayPid) {
        $proc = Get-Process -Id $gatewayPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Stopping PID $gatewayPid (from gateway.pid)..." -ForegroundColor Cyan
            Stop-Process -Id $gatewayPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            $gatewayStopped = $true
            Write-Host "  PID $gatewayPid stopped." -ForegroundColor Green
        } else {
            Write-Host "  PID $gatewayPid from gateway.pid not running." -ForegroundColor Gray
        }
    }
}

# Step 3: Always kill whatever is still listening on port 9119 (handles stale PID file)
$portPid = (netstat -ano | Select-String "127.0.0.1:9119.*LISTENING" | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1)
if ($portPid) {
    Write-Host "  Killing listener on port 9119 (PID $portPid)..." -ForegroundColor Cyan
    taskkill /F /PID $portPid 2>&1 | Out-Null
    Stop-Process -Id ([int]$portPid) -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $gatewayStopped = $true
    Write-Host "  Gateway listener killed." -ForegroundColor Green
} elseif (-not $gatewayStopped) {
    Write-Host "  No process listening on port 9119 (already stopped)." -ForegroundColor Gray
}

# Verify
Start-Sleep -Seconds 1
$stillUp = netstat -ano | Select-String "127.0.0.1:9119.*LISTENING"
if ($stillUp) {
    $stillPid = ($stillUp -replace '.*\s+(\d+)$','$1').Trim()
    Write-Host "  WARNING: Port 9119 still held by PID $stillPid (may be elevated process — close terminal that started gateway)." -ForegroundColor Red
} else {
    Write-Host "  Port 9119 clear." -ForegroundColor Green
}

# --- 2. Open Notebook ---
Write-Host ""
Write-Host "[2/6] Open Notebook" -ForegroundColor White
$onComposeDir = "C:\Data\Hermes\~\open-notebook-local"
$onComposeFile = Join-Path $onComposeDir "docker-compose.yml"
if (Test-Path $onComposeFile) {
    if (Test-DockerRunning) {
        Write-Host "  Running docker compose down..." -ForegroundColor Cyan
        Push-Location $onComposeDir
        try { docker compose down 2>&1 | ForEach-Object { Write-Host "    $_" } }
        finally { Pop-Location }
        Write-Host "  Open Notebook stack stopped." -ForegroundColor Green
    } else {
        Write-Host "  Docker not running -- skipping." -ForegroundColor Gray
    }
} else {
    Write-Host "  Open Notebook compose file not found at $onComposeFile -- skipping." -ForegroundColor Yellow
}

# --- 3. Honcho stack ---
Write-Host ""
Write-Host "[3/6] Honcho stack" -ForegroundColor White
$composeDir = "C:\honcho"
$composeFile = Join-Path $composeDir "docker-compose.yml"
if (Test-Path $composeFile) {
    if (Test-DockerRunning) {
        Write-Host "  Running docker compose down..." -ForegroundColor Cyan
        Push-Location $composeDir
        try { docker compose down 2>&1 | ForEach-Object { Write-Host "    $_" } }
        finally { Pop-Location }
        Write-Host "  Honcho stack stopped." -ForegroundColor Green
    } else {
        Write-Host "  Docker not running -- skipping." -ForegroundColor Gray
    }
} else {
    Write-Host "  Honcho compose file not found at $composeFile -- skipping." -ForegroundColor Yellow
}

# --- 4. Qdrant ---
Write-Host ""
Write-Host "[4/6] Qdrant" -ForegroundColor White
if (Test-DockerRunning) {
    $qdrantStatus = docker ps -a --filter "name=qdrant-research" --format "{{.Status}}" 2>$null
    if ($qdrantStatus -like "Up*") {
        docker stop qdrant-research | Out-Null
        Write-Host "  Qdrant stopped." -ForegroundColor Green
    } else {
        Write-Host "  Qdrant already stopped ($qdrantStatus)." -ForegroundColor Gray
    }
} else {
    Write-Host "  Docker not running -- skipping." -ForegroundColor Gray
}

# --- 5. Speaches ---
Write-Host ""
Write-Host "[5/6] Speaches" -ForegroundColor White
if (Test-DockerRunning) {
    $speachesStatus = docker ps -a --filter "name=speaches" --format "{{.Status}}" 2>$null
    if ($speachesStatus -like "Up*") {
        docker stop speaches | Out-Null
        Write-Host "  Speaches stopped." -ForegroundColor Green
    } else {
        Write-Host "  Speaches already stopped ($speachesStatus)." -ForegroundColor Gray
    }
} else {
    Write-Host "  Docker not running -- skipping." -ForegroundColor Gray
}

# --- 6. WSL hermes-tools (optional) ---
Write-Host ""
Write-Host "[6/6] WSL hermes-tools" -ForegroundColor White
if ($TerminateWSL) {
    Write-Host "  Terminating hermes-tools WSL distro..." -ForegroundColor Cyan
    wsl.exe --terminate hermes-tools 2>$null
    Write-Host "  hermes-tools terminated." -ForegroundColor Green
} else {
    Write-Host "  Skipped (use -TerminateWSL to also stop hermes-tools)." -ForegroundColor Gray
}

# --- Docker Desktop (optional) ---
if ($StopDocker) {
    Write-Host ""
    Write-Host "[+] Stopping Docker Desktop..." -ForegroundColor White
    $desktopProc = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if ($desktopProc) {
        Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
        Write-Host "  Docker Desktop stopped." -ForegroundColor Green
    } else {
        Write-Host "  Docker Desktop not running." -ForegroundColor Gray
    }
}

# Final container summary
Write-Host ""
Show-ContainerSummary

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Yellow
Write-Host "  Hermes Stack Stopped" -ForegroundColor Yellow
Write-Host "=======================================================" -ForegroundColor Yellow
Write-Host "  Log: $LogFile" -ForegroundColor Gray
Write-Host ""
Write-Host "  To restart:  powershell -File `"$ProjectRoot\scripts\startup_hermes.ps1`"" -ForegroundColor White
Write-Host ""

Stop-Transcript
