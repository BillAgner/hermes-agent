#Requires -Version 5.1
<#
.SYNOPSIS
    Clean install of Hermes Agent v0.17.0 at C:\Data\Hermes_0.17.0 0.17.0

.DESCRIPTION
    Parallel install alongside live C:\Data\Hermes_0.17.0 (which stays on v0.16.0 + cp312).
    Uses Python 3.12 + uv for venv creation, mirrors live config files but skips
    skills/ per Bill's instruction (will evaluate skills after initial install).

    Idempotent: re-running will detect existing state and skip done steps.
    Self-verifying: each step ends with [OK], [FAIL], or [SKIP].
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference   = 'SilentlyContinue'

# === Configuration ===========================================================
$InstallRoot       = 'C:\Data\Hermes_0.17.0 0.17.0'
$HermesRepo        = 'https://github.com/NousResearch/hermes-agent.git'
$BranchOrTag       = 'v2026.6.19'
$VenvDir           = Join-Path $InstallRoot 'venv'
$Python312         = 'C:\Users\bobup\AppData\Local\Programs\Python\Python312\python.exe'
$Uv                = 'C:\Users\bobup\.local\bin\uv.exe'
$LiveHermes        = 'C:\Data\Hermes_0.17.0'

# Trial-specific port assignment (live is 9119/9118; trial is 9121/9122)
$GatewayPort       = 9121
$TradeJournalPort  = 9122

# === Helpers ==================================================================
function Write-Step($id, $msg) {
    Write-Host ""
    Write-Host "[$id] $msg" -ForegroundColor Cyan
}

function Write-OK($id, $detail = '') {
    if ($detail) { Write-Host "[$id] [OK] $detail" -ForegroundColor Green }
    else          { Write-Host "[$id] [OK]"                -ForegroundColor Green }
}

function Write-FAIL($id, $msg) {
    Write-Host "[$id] [FAIL] $msg" -ForegroundColor Red
    exit 1
}

function Write-SKIP($id, $msg) {
    Write-Host "[$id] [SKIP] $msg" -ForegroundColor Yellow
}

# === Step 0: Pre-flight ======================================================
Write-Step '0' 'Pre-flight checks'

if ((Test-Path $InstallRoot) -and -not (Test-Path (Join-Path $InstallRoot 'pyproject.toml'))) {
    Write-FAIL '0' "$InstallRoot exists but has no pyproject.toml. Remove it first."
}
if (-not (Test-Path $Python312)) {
    Write-FAIL '0' "Python 3.12 not found at $Python312"
}
if (-not (Test-Path $Uv)) {
    Write-FAIL '0' "uv not found at $Uv"
}
if (-not (Test-Path $LiveHermes)) {
    Write-FAIL '0' "Live Hermes not found at $LiveHermes -config source-"
}
$freeGB = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
if ($freeGB -lt 10) {
    Write-FAIL '0' "Only $freeGB GB free on C: -need >= 10 GB-"
}
Write-OK '0' "Python 3.12 [OK]  uv [OK]  live Hermes [OK]  disk free: $freeGB GB"

# === Step 1: Clone v0.17.0 ===================================================
Write-Step '1' "Clone Hermes v0.17.0 ($BranchOrTag) from NousResearch/hermes-agent"

# git clone extracts repo contents directly into target dir (no subdir wrapper)
if (Test-Path (Join-Path $InstallRoot 'pyproject.toml')) {
    Write-SKIP '1' "$InstallRoot already has pyproject.toml - clone done in prior run"
} else {
    git clone --branch $BranchOrTag --depth 1 $HermesRepo $InstallRoot
    if ($LASTEXITCODE -ne 0) { Write-FAIL '1' 'git clone failed' }
    if (-not (Test-Path (Join-Path $InstallRoot 'pyproject.toml'))) {
        Write-FAIL '1' 'pyproject.toml missing after clone'
    }
    Write-OK '1' "cloned to $InstallRoot"
}

# === Step 2: Create venv on cp312 + install hermes-agent ======================
Write-Step '2' 'Create venv on cp312 + install hermes-agent + deps'

if (Test-Path (Join-Path $VenvDir 'Scripts\hermes.exe')) {
    Write-SKIP '2' "venv at $VenvDir already has hermes.exe -deps install done in prior run-"
} else {
    & $Python312 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-FAIL '2' 'venv creation failed' }

    # Use uv to install hermes-agent editable; uv reads pyproject.toml
    & $Uv pip install --python "$VenvDir\Scripts\python.exe" -e $InstallRoot
    if ($LASTEXITCODE -ne 0) { Write-FAIL '2' 'uv pip install failed' }

    # Verify hermes.exe was created
    if (-not (Test-Path (Join-Path $VenvDir 'Scripts\hermes.exe'))) {
        Write-FAIL '2' 'hermes.exe not found in venv after install'
    }
    Write-OK '2' "venv: $VenvDir  -hermes.exe present-"
}

# === Step 3: Copy config files from live (skip skills/) ======================
Write-Step '3' 'Copy config files from live (skip skills/ per Bill)'

Copy-Item -Path "$LiveHermes\.env"           -Destination "$InstallRoot\.env"           -Force
if (-not (Test-Path "$InstallRoot\.env")) { Write-FAIL '3' ".env copy failed" }

Copy-Item -Path "$LiveHermes\config.yaml"    -Destination "$InstallRoot\config.yaml"    -Force
if (-not (Test-Path "$InstallRoot\config.yaml")) { Write-FAIL '3' "config.yaml copy failed" }

Copy-Item -Path "$LiveHermes\channel_directory.json" -Destination "$InstallRoot\channel_directory.json" -Force
if (-not (Test-Path "$InstallRoot\channel_directory.json")) { Write-FAIL '3' "channel_directory.json copy failed" }

Copy-Item -Path "$LiveHermes\mcp-servers"    -Destination "$InstallRoot\mcp-servers"    -Recurse -Force
if (-not (Test-Path "$InstallRoot\mcp-servers")) { Write-FAIL '3' "mcp-servers copy failed" }

Copy-Item -Path "$LiveHermes\cron"           -Destination "$InstallRoot\cron"           -Recurse -Force
if (-not (Test-Path "$InstallRoot\cron")) { Write-FAIL '3' "cron copy failed" }

Copy-Item -Path "$LiveHermes\data"           -Destination "$InstallRoot\data"           -Recurse -Force
if (-not (Test-Path "$InstallRoot\data")) { Write-FAIL '3' "data copy failed" }

# Skills are SKIPPED per Bill (will evaluate later)
Write-OK '3' 'copied: .env, config.yaml, channel_directory.json, mcp-servers/, cron/, data/ -- SKIPPED: skills/'

# === Step 4: Adjust config.yaml ports for trial ===============================
Write-Step '4' "Adjust config.yaml: gateway_port=$GatewayPort, trade_journal_port=$TradeJournalPort"

$configPath = Join-Path $InstallRoot 'config.yaml'
$config     = Get-Content $configPath -Raw

# Trial gets its own ports so live stays untouched
# Trial gets its own ports so live stays untouched
# Use a Python helper to avoid PowerShell's aggressive regex parsing
# Pass values via environment variables to avoid PowerShell string interpolation issues
$pyTmpPath = Join-Path $InstallRoot '_patch_config.py'

$pyContent = @"
import os, re
config_path          = os.environ['PATCH_CONFIG_PATH']
trial_venv_scripts   = os.environ['PATCH_TRIAL_VENV_SCRIPTS']
gateway_port         = os.environ['PATCH_GATEWAY_PORT']
trade_journal_port   = os.environ['PATCH_TRADE_JOURNAL_PORT']

with open(config_path, 'r', encoding='utf-8') as f:
    cfg = f.read()
cfg = re.sub(r'^(\s*)gateway_port:\s*\d+',       r'\1gateway_port: ' + gateway_port,       cfg, flags=re.M)
cfg = re.sub(r'^(\s*)trade_journal_port:\s*\d+',  r'\1trade_journal_port: ' + trade_journal_port, cfg, flags=re.M)
cfg = cfg.replace(r'C:\Data\Hermes_0.17.0 0.17.0\venv\Scripts', trial_venv_scripts)
with open(config_path, 'w', encoding='utf-8') as f:
    f.write(cfg)
print('OK')
"@

Set-Content -Path $pyTmpPath -Value $pyContent

# Pass values via env vars
$env:PATCH_CONFIG_PATH         = $configPath
$env:PATCH_TRIAL_VENV_SCRIPTS  = "$VenvDir\Scripts"
$env:PATCH_GATEWAY_PORT        = $GatewayPort
$env:PATCH_TRADE_JOURNAL_PORT  = $TradeJournalPort

& $Python312 $pyTmpPath
$pyExit = $LASTEXITCODE

# Clean up env vars
Remove-Item Env:PATCH_CONFIG_PATH -ErrorAction SilentlyContinue
Remove-Item Env:PATCH_TRIAL_VENV_SCRIPTS -ErrorAction SilentlyContinue
Remove-Item Env:PATCH_GATEWAY_PORT -ErrorAction SilentlyContinue
Remove-Item Env:PATCH_TRADE_JOURNAL_PORT -ErrorAction SilentlyContinue
Remove-Item -Path $pyTmpPath -Force

if ($pyExit -ne 0) { Write-FAIL '4' 'config.yaml update failed' }
Write-OK '4' 'ports adjusted + MCP paths repointed to trial venv'

# === Step 5: Wrapper scripts (scrub env-leak pattern) =========================
Write-Step '5' 'Create wrapper scripts (hermes017.cmd, hermes017-py.cmd)'

$hermes017Cmd = @"
@echo off
REM Hermes 0.17.0 launcher -- starts the parallel v0.17.0 install with a clean
REM runtime environment. Scrubs PYTHONPATH/VIRTUAL_ENV/HERMES_HOME inherited
REM from the parent shell so the trial reads its own config + venv.

set PYTHONPATH=
set VIRTUAL_ENV=$VenvDir
set HERMES_HOME=$InstallRoot
set "PATH=$VenvDir\Scripts;%PATH%"
cd /d "$InstallRoot"
"$VenvDir\Scripts\hermes.exe" %*
"@

$hermes017PyCmd = @"
@echo off
REM Hermes 0.17.0 Python launcher -- invokes the trial's Python with a clean env.

set PYTHONPATH=
set VIRTUAL_ENV=$VenvDir
set HERMES_HOME=$InstallRoot
cd /d "$InstallRoot"
"$VenvDir\Scripts\python.exe" %*
"@

$wrapperPath1 = Join-Path $InstallRoot 'hermes017.cmd'
$wrapperPath2 = Join-Path $InstallRoot 'hermes017-py.cmd'
Set-Content -Path $wrapperPath1 -Value $hermes017Cmd  -Encoding ASCII
Set-Content -Path $wrapperPath2 -Value $hermes017PyCmd -Encoding ASCII

if (-not (Test-Path $wrapperPath1)) { Write-FAIL '5' "hermes017.cmd write failed" }
if (-not (Test-Path $wrapperPath2)) { Write-FAIL '5' "hermes017-py.cmd write failed" }
Write-OK '5' "wrappers: $wrapperPath1, $wrapperPath2"

# === Step 6: Smoke test ======================================================
Write-Step '6' 'Smoke test -version + import'

# Scrub env-leak variables so the trial reads its own venv, not the live one
$env:PYTHONPATH  = ''
$env:VIRTUAL_ENV = $VenvDir
$env:HERMES_HOME = $InstallRoot

$hermesExe = Join-Path $VenvDir 'Scripts\hermes.exe'
$versionOutput = & $hermesExe --version 2>&1
if ($LASTEXITCODE -ne 0) { Write-FAIL '6' "hermes.exe --version failed: $versionOutput" }

$allVersionText = ($versionOutput -join "`n")
if ($allVersionText -notmatch 'v0\.17\.0') {
    Write-FAIL '6' "Expected v0.17.0 in version output, got: $allVersionText"
}
if ($allVersionText -notmatch '3\.12\.10') {
    Write-FAIL '6' "Expected Python 3.12.10 in version output, got: $allVersionText"
}

$pythonExe = Join-Path $VenvDir 'Scripts\python.exe'

# Use a temp script file to avoid PowerShell quote-escaping issues with -c
$smokeTmpPath = Join-Path $InstallRoot '_smoke_test.py'
$smokeContent = @'
import hermes_cli
from agent import agent_init
print('imports OK')
'@
Set-Content -Path $smokeTmpPath -Value $smokeContent

$importCheck = & $pythonExe $smokeTmpPath 2>&1
Remove-Item -Path $smokeTmpPath -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { Write-FAIL '6' "imports failed: $importCheck" }

Write-OK '6' 'version: v0.17.0 + Python 3.12.10 confirmed'

# === Done ====================================================================
Write-Host ""
Write-Host "========================================="  -ForegroundColor Green
Write-Host "  Install complete"                          -ForegroundColor Green
Write-Host "========================================="  -ForegroundColor Green
Write-Host ""
Write-Host "Trial location:  $InstallRoot"               -ForegroundColor Cyan
Write-Host "Run gateway:     $wrapperPath1 gateway run --port $GatewayPort"
Write-Host "Run dashboard:   http://127.0.0.1:$GatewayPort"
Write-Host "Direct Python:   $wrapperPath2 -c `"import sys; print(sys.version)`""
Write-Host ""
Write-Host "Live stays on v0.16.0 + cp312 (untouched)."
Write-Host ""