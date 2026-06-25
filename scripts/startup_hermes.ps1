<#
.SYNOPSIS
    Hermes Container Stack Startup
.DESCRIPTION
    Starts the Docker-backed services Hermes needs after a fresh boot:
      1. Docker engine  (waits up to 3 min for com.docker.service to bring it up)
      2. Honcho stack   (docker compose up at C:\honcho, API on :8000)
      3. Qdrant         (standalone container, ports 6333/6334)
      4. Speaches       (standalone container, port 8004)
      5. Open Notebook  (docker compose up at C:\Data\Hermes\~\open-notebook-local, UI :8502, API :5055)

    The Hermes gateway itself is handled by the separate "Hermes_Gateway" task.

    Runs headlessly via Task Scheduler (AtStartup, S4U as bobup, 1min delay).
    Safe to run manually at any time -- all steps are idempotent.
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Data\Hermes_0.17.0"

# --- Logging ---
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("startup_hermes_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
Start-Transcript -Path $LogFile -Append
Write-Host "Log: $LogFile"

# Keep last 10 startup logs
Get-ChildItem "$LogDir\startup_hermes_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 10 |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Ensure docker CLI is on PATH (Task Scheduler SYSTEM/S4U sessions have a minimal PATH)
$DockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ((Test-Path $DockerBin) -and ($env:PATH -notlike "*$DockerBin*")) {
    $env:PATH = "$DockerBin;$env:PATH"
    Write-Host "Added $DockerBin to PATH"
}

. "$PSScriptRoot\_common.ps1"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Hermes -- Booting Container Stack" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# --- 0. WSL hermes-tools ---
Write-Host "[0/5] WSL hermes-tools" -ForegroundColor White
Start-WslHermesTools

# --- 1. Docker engine ---
Write-Host "[1/5] Docker engine" -ForegroundColor White
if (-not (Wait-ForDocker)) {
    Write-Host "  Docker engine unavailable after timeout. Containers will not start." -ForegroundColor Red
    Show-ContainerSummary
    Stop-Transcript
    exit 1
}

# --- 2. Honcho stack ---
Write-Host "[2/5] Honcho stack" -ForegroundColor White
Ensure-HonchoStack
Wait-ForHoncho | Out-Null

# --- 3. Qdrant ---
Write-Host "[3/5] Qdrant" -ForegroundColor White
Ensure-QdrantVolume
Ensure-QdrantContainer
Wait-ForQdrant | Out-Null

# --- 4. Speaches ---
Write-Host "[4/5] Speaches" -ForegroundColor White
Ensure-SpeachesContainer
Wait-ForSpeaches | Out-Null

# --- 5. Open Notebook ---
Write-Host "[5/5] Open Notebook" -ForegroundColor White
Ensure-OpenNotebookStack
Wait-ForOpenNotebook | Out-Null

Show-ContainerSummary

# --- 5. Verify WSL hermes-tools still running ---
Write-Host ""
Write-Host "[5/5] WSL hermes-tools final check" -ForegroundColor White
Start-WslHermesTools

# --- 6. Gateway + platform health check ---
Write-Host ""
Write-Host "[6/6] Gateway and platform check" -ForegroundColor White

$GatewayUrl = "http://127.0.0.1:9119"
$GatewayReady = $false
$GatewayToken = $null
$elapsed = 0
$GatewayTimeout = 120  # gateway task fires in parallel; give it up to 2 min

while ($elapsed -lt $GatewayTimeout) {
    try {
        $page = Invoke-WebRequest -Uri "$GatewayUrl/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($page.Content -match '__HERMES_SESSION_TOKEN__="([A-Za-z0-9_\-]+)"') {
            $GatewayToken = $Matches[1]
            $GatewayReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 5
    $elapsed += 5
    if ($elapsed % 15 -eq 0) {
        Write-Host "  Waiting for gateway... (${elapsed}/${GatewayTimeout}s)" -ForegroundColor Gray
    }
}

if (-not $GatewayReady) {
    Write-Host "  WARNING: Gateway not responding after ${GatewayTimeout}s. Check Hermes_Gateway task." -ForegroundColor Red
} else {
    $gwPid = (netstat -ano 2>$null | Select-String "127.0.0.1:9119.*LISTENING" |
              ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1)
    Write-Host "  Gateway ready. PID $gwPid" -ForegroundColor Green

    # Platform status
    try {
        $r = Invoke-WebRequest "$GatewayUrl/api/messaging/platforms" -UseBasicParsing -TimeoutSec 5 `
            -Headers @{ "Authorization" = "Bearer $GatewayToken" } -ErrorAction Stop
        $platforms = ($r.Content | ConvertFrom-Json).platforms
        foreach ($p in $platforms | Where-Object { $_.enabled }) {
            $color = if ($p.state -eq "connected") { "Green" } else { "Red" }
            $icon  = if ($p.state -eq "connected") { "[OK]" } else { "[!!]" }
            Write-Host ("  {0} {1,-12} {2}" -f $icon, $p.id, $p.state) -ForegroundColor $color
        }
    } catch {
        Write-Host "  WARNING: Could not read platform status: $_" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  Hermes Ready" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  Log: $LogFile" -ForegroundColor Gray
Write-Host ""

Stop-Transcript
