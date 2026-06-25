<#
.SYNOPSIS
    Shared helpers for Hermes stack management scripts.
.DESCRIPTION
    Dot-source this file from other scripts:
      . "$PSScriptRoot\_common.ps1"

    Hermes required containers:
      - Honcho stack (compose at C:\honcho): honcho-api-1, honcho-database-1, honcho-redis-1, honcho-deriver-1
      - Qdrant (standalone):                 qdrant-research  (ports 6333/6334)
      - Speaches (standalone):               speaches         (port 8004)
      - Open Notebook (compose at C:\Data\Hermes\~\open-notebook-local): UI :8502, API :5055
#>

function Test-DockerRunning {
    try {
        $null = docker info 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Wait-ForDocker {
    param([int]$TimeoutSeconds = 180)

    if (Test-DockerRunning) {
        Write-Host "  Docker daemon is running." -ForegroundColor Green
        return $true
    }

    # On this machine, com.docker.service alone does not bring up the engine --
    # Docker Desktop (the GUI process) must be running to initialize the WSL2
    # backend and start dockerd. Launch it if it's not already running.
    $desktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    $desktopRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if (-not $desktopRunning) {
        if (Test-Path $desktopExe) {
            Write-Host "  Launching Docker Desktop..." -ForegroundColor Yellow
            Start-Process $desktopExe -ErrorAction SilentlyContinue
        } else {
            Write-Host "  WARNING: Docker Desktop.exe not found at $desktopExe" -ForegroundColor Red
        }
    } else {
        Write-Host "  Docker Desktop is running -- waiting for engine..." -ForegroundColor Yellow
    }

    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        Start-Sleep -Seconds 5
        $elapsed += 5
        if (Test-DockerRunning) {
            Write-Host "  Docker daemon ready (${elapsed}s)." -ForegroundColor Green
            return $true
        }
        Write-Host "  Waiting for Docker engine... (${elapsed}/${TimeoutSeconds}s)" -ForegroundColor Gray
    }

    Write-Warning "Docker engine did not become ready within ${TimeoutSeconds}s."
    return $false
}

function Ensure-HonchoStack {
    $composeDir = "C:\honcho"
    $composeFile = Join-Path $composeDir "docker-compose.yml"
    if (-not (Test-Path $composeFile)) {
        Write-Warning "  Honcho compose file not found at $composeFile -- skipping."
        return
    }

    # All four containers already Up? Skip the noisy compose call.
    $expected = @("honcho-api-1", "honcho-database-1", "honcho-redis-1", "honcho-deriver-1")
    $up = ($expected | Where-Object {
        (docker ps -a --filter "name=$_" --format "{{.Status}}" 2>$null) -like "Up*"
    }).Count
    if ($up -eq $expected.Count) {
        Write-Host "  Honcho stack already running ($up/$($expected.Count) containers Up)." -ForegroundColor Green
        return
    }

    Write-Host "  Starting Honcho stack (docker compose up -d)..." -ForegroundColor Cyan
    Push-Location $composeDir
    try {
        # Use cmd.exe to avoid PS5.1 treating docker stderr as terminating errors
        $out = cmd /c "docker compose up -d 2>&1"
        $out | ForEach-Object { Write-Host "    $_" }
    }
    finally { Pop-Location }
}

function Wait-ForHoncho {
    param([int]$TimeoutSeconds = 60)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Write-Host "  Honcho API ready." -ForegroundColor Green; return $true }
        } catch {}
        Start-Sleep -Seconds 2; $elapsed += 2
        Write-Host "  Waiting for Honcho API... (${elapsed}/${TimeoutSeconds}s)" -ForegroundColor Gray
    }
    Write-Warning "Honcho did not become ready within ${TimeoutSeconds}s."
    return $false
}

function Ensure-QdrantVolume {
    $existing = docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -eq "qdrant_research" }
    if ($existing) {
        Write-Host "  Volume 'qdrant_research' exists." -ForegroundColor Green
        return
    }
    docker volume create qdrant_research | Out-Null
    Write-Host "  Volume 'qdrant_research' created." -ForegroundColor Cyan
}

function Ensure-QdrantContainer {
    $container = docker ps -a --filter "name=qdrant-research" --format "{{.Status}}" 2>$null
    if (-not $container) {
        Write-Host "  Creating Qdrant container..." -ForegroundColor Cyan
        docker run -d `
            --name qdrant-research `
            --restart unless-stopped `
            -p 6333:6333 -p 6334:6334 `
            -v qdrant_research:/qdrant/storage `
            qdrant/qdrant:latest | Out-Null
        Write-Host "  Qdrant container created and started." -ForegroundColor Green
        return
    }
    if ($container -like "Up*") {
        Write-Host "  Qdrant container is running." -ForegroundColor Green
        return
    }
    Write-Host "  Starting stopped Qdrant container..." -ForegroundColor Yellow
    docker start qdrant-research | Out-Null
    docker update --restart unless-stopped qdrant-research | Out-Null
    Write-Host "  Qdrant container started." -ForegroundColor Green
}

function Wait-ForQdrant {
    param([int]$TimeoutSeconds = 45)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:6333/readyz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Write-Host "  Qdrant API ready." -ForegroundColor Green; return $true }
        } catch {}
        Start-Sleep -Seconds 2; $elapsed += 2
        Write-Host "  Waiting for Qdrant API... (${elapsed}/${TimeoutSeconds}s)" -ForegroundColor Gray
    }
    Write-Warning "Qdrant did not become ready within ${TimeoutSeconds}s."
    return $false
}

function Ensure-SpeachesContainer {
    $container = docker ps -a --filter "name=speaches" --format "{{.Status}}" 2>$null
    if (-not $container) {
        Write-Host "  Creating Speaches container..." -ForegroundColor Cyan
        docker run -d `
            --name speaches `
            --restart unless-stopped `
            -p 8004:8000 `
            ostapw/speaches:latest | Out-Null
        Write-Host "  Speaches container created and started." -ForegroundColor Green
        return
    }
    if ($container -like "Up*") {
        Write-Host "  Speaches container is running." -ForegroundColor Green
        return
    }
    Write-Host "  Starting stopped Speaches container..." -ForegroundColor Yellow
    docker start speaches | Out-Null
    docker update --restart unless-stopped speaches | Out-Null
    Write-Host "  Speaches container started -- waiting 5s for listener..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
}

function Wait-ForSpeaches {
    param([int]$TimeoutSeconds = 45)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        $code = curl.exe -s -o NUL -w "%{http_code}" --max-time 3 http://127.0.0.1:8004/v1/models 2>$null
        if ($code -eq "200") { Write-Host "  Speaches API ready." -ForegroundColor Green; return $true }
        Start-Sleep -Seconds 2; $elapsed += 2
        Write-Host "  Waiting for Speaches... (${elapsed}/${TimeoutSeconds}s)" -ForegroundColor Gray
    }
    Write-Warning "Speaches did not respond within ${TimeoutSeconds}s -- transcription unavailable."
    return $false
}

function Ensure-OpenNotebookStack {
    $composeDir = "C:\Data\Hermes\~\open-notebook-local"
    $composeFile = Join-Path $composeDir "docker-compose.yml"
    if (-not (Test-Path $composeFile)) {
        Write-Warning "  Open Notebook compose file not found at $composeFile -- skipping."
        return
    }

    $expected = @("open-notebook-local-surrealdb-1", "open-notebook-local-open_notebook-1")
    $up = ($expected | Where-Object {
        (docker ps -a --filter "name=$_" --format "{{.Status}}" 2>$null) -like "Up*"
    }).Count
    if ($up -eq $expected.Count) {
        Write-Host "  Open Notebook stack already running ($up/$($expected.Count) containers Up)." -ForegroundColor Green
        return
    }

    Write-Host "  Starting Open Notebook stack (docker compose up -d)..." -ForegroundColor Cyan
    Push-Location $composeDir
    try {
        $out = cmd /c "docker compose up -d 2>&1"
        $out | ForEach-Object { Write-Host "    $_" }
    }
    finally { Pop-Location }
}

function Wait-ForOpenNotebook {
    param([int]$TimeoutSeconds = 60)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:5055/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Write-Host "  Open Notebook API ready." -ForegroundColor Green; return $true }
        } catch {}
        Start-Sleep -Seconds 2; $elapsed += 2
        Write-Host "  Waiting for Open Notebook API... (${elapsed}/${TimeoutSeconds}s)" -ForegroundColor Gray
    }
    Write-Warning "Open Notebook did not become ready within ${TimeoutSeconds}s."
    return $false
}

function Start-WslHermesTools {
    # Boot hermes-tools and leave a persistent keep-alive so the distro
    # stays in "Running" state (green in the health dashboard).
    # The gateway's WSL probe reads wsl --list state (32ms) rather than
    # executing a command, so it always shows Stopped between invocations.
    param([int]$SleepHours = 12)

    # Wake the distro
    $ping = (& wsl.exe -d hermes-tools -- echo "OK" 2>&1) -replace "`0",""
    if ($ping -notmatch "OK") {
        Write-Host "  WARNING: hermes-tools did not respond: $ping" -ForegroundColor Red
        return
    }

    # Check if a keep-alive is already running (idempotent)
    $tag = "hermes-keepalive"
    $check = (& wsl.exe -d hermes-tools -- bash -c "pgrep -f $tag > /dev/null 2>&1 && echo ALIVE || echo DEAD" 2>&1) -replace "`0",""
    if ($check -match "ALIVE") {
        Write-Host "  hermes-tools running (keep-alive active)." -ForegroundColor Green
        return
    }

    # Start a background sleep tagged so we can find it again
    & wsl.exe -d hermes-tools -- bash -c "nohup bash -c 'exec -a $tag sleep ${SleepHours}h' >/dev/null 2>&1 &" 2>$null
    Start-Sleep -Milliseconds 500

    # Confirm Running state
    $state = (wsl.exe --list --verbose 2>&1) -replace "`0","" | Select-String "hermes-tools"
    if ($state -match "Running") {
        Write-Host "  hermes-tools running (keep-alive started, ${SleepHours}h)." -ForegroundColor Green
    } else {
        Write-Host "  hermes-tools responding but keep-alive may need a moment." -ForegroundColor Yellow
    }
}

function Show-ContainerSummary {
    $expected = @("honcho-api-1", "honcho-database-1", "honcho-redis-1", "honcho-deriver-1", "qdrant-research", "speaches", "open-notebook-local-surrealdb-1", "open-notebook-local-open_notebook-1")
    Write-Host ""
    Write-Host "  Container status:" -ForegroundColor White
    if (-not (Test-DockerRunning)) {
        Write-Host "    (docker engine not reachable -- cannot query containers)" -ForegroundColor Yellow
        return
    }
    foreach ($c in $expected) {
        $status = docker ps -a --filter "name=$c" --format "{{.Status}}" 2>$null
        if     ($status -like "Up*") { Write-Host "    [OK]   $c  ($status)" -ForegroundColor Green  }
        elseif ($status)             { Write-Host "    [STOP] $c  ($status)" -ForegroundColor Yellow }
        else                         { Write-Host "    [--]   $c  (not created)" -ForegroundColor Gray   }
    }
}