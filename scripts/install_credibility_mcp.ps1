# install_credibility_mcp.ps1
# Installs credibility-mcp into the Hermes agent venv (editable), creates the
# skills junction, and registers the MCP server in config.yaml.
#
# Self-verifies with [OK]/[FAIL] at each step. Idempotent — safe to re-run.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\install_credibility_mcp.ps1

$ErrorActionPreference = "Stop"

# --- Paths ------------------------------------------------------------------
$HERMES_HOME      = "C:\Data\Hermes_0.17.0"
$HERMES_VENV_PY   = Join-Path $HERMES_HOME "hermes-agent\venv\Scripts\python.exe"
$SRC_ROOT         = Join-Path $HERMES_HOME "~\credibility-mcp\packages\credibility-mcp"
$SKILL_DST        = Join-Path $HERMES_HOME "skills\research\source-credibility"
$SKILL_SRC        = Join-Path $SRC_ROOT "skills\source-credibility"
$CACHE_DIR        = Join-Path $HERMES_HOME "cache\credibility_log"

function Step($msg) { Write-Host "[install] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]      $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL]    $msg" -ForegroundColor Red; exit 1 }

# --- Pre-flight --------------------------------------------------------------
Step "pre-flight checks"
if (-not (Test-Path $HERMES_VENV_PY)) {
    Fail "Hermes venv python not found at $HERMES_VENV_PY"
}
Ok "Hermes venv python: $HERMES_VENV_PY"

if (-not (Test-Path $SRC_ROOT)) {
    Fail "Source package not found at $SRC_ROOT"
}
Ok "Source package present"

# --- 1. Editable install into Hermes venv ------------------------------------
Step "pip install -e $SRC_ROOT"
& $HERMES_VENV_PY -m pip install -e $SRC_ROOT --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (exit $LASTEXITCODE)" }
Ok "editable install succeeded"

# Verify binary is on PATH inside venv
$BIN = Join-Path $HERMES_HOME "hermes-agent\venv\Scripts\credibility-mcp.exe"
if (-not (Test-Path $BIN)) {
    Fail "credibility-mcp.exe not found at $BIN"
}
Ok "binary present: $BIN"

# --- 2. Skills junction -------------------------------------------------------
Step "create skills junction"
if (-not (Test-Path $SKILL_DST)) {
    # Junction (mklink /J) does NOT require admin, unlike symlinks
    & cmd //c mklink /J $SKILL_DST $SKILL_SRC | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "mklink junction failed (exit $LASTEXITCODE)" }
    Ok "junction created: $SKILL_DST -> $SKILL_SRC"
} else {
    Ok "junction already exists at $SKILL_DST (skipping)"
}

# --- 3. Cache directory for log persistence -----------------------------------
Step "create log cache directory"
if (-not (Test-Path $CACHE_DIR)) {
    New-Item -ItemType Directory -Path $CACHE_DIR | Out-Null
}
Ok "log cache: $CACHE_DIR"

# --- 4. Smoke-test the scorer (no MCP layer) ---------------------------------
Step "smoke-test the scorer"
$SMOKE = @"
from credibility_mcp.scorer import score_source
r = score_source('https://www.cmegroup.com/markets/silver.html', title='COMEX silver quotes')
assert r.score > 0.7, f'expected high score for cmegroup.com, got {r.score}'
assert r.source_class == 'primary_data', f'expected primary_data, got {r.source_class}'
print(f'  cmegroup.com -> score={r.score} class={r.source_class} action={r.threshold_action}')
r = score_source('https://reddit.com/r/Silverbugs/comments/abc/silver_squeeze_now')
assert r.score < 0.7, f'expected lower score for reddit, got {r.score}'
assert r.source_class == 'niche_forum', f'expected niche_forum, got {r.source_class}'
print(f'  reddit       -> score={r.score} class={r.source_class} action={r.threshold_action}')
r = score_source('https://example.com/best-silver-stocks-2026-top-10')
assert r.source_class == 'content_farm', f'expected content_farm, got {r.source_class}'
print(f'  content-farm -> score={r.score} class={r.source_class} action={r.threshold_action}')
print('smoke-test ok')
"@
$SMOKE_FILE = Join-Path $env:TEMP "credibility_smoke_test.py"
[System.IO.File]::WriteAllText($SMOKE_FILE, $SMOKE)
& $HERMES_VENV_PY $SMOKE_FILE
if ($LASTEXITCODE -ne 0) { Fail "smoke-test failed (see output above)" }
Remove-Item $SMOKE_FILE -ErrorAction SilentlyContinue
Ok "scorer smoke-test passed"

# --- 5. Register MCP server via hermes CLI -----------------------------------
# Per the mcp-server-setup skill: never edit config.yaml directly; use
# `hermes mcp add` with stdin piped to "Y" to enable all tools non-interactively.
Step "register MCP server via hermes mcp add"
$HERMES = Join-Path $HERMES_HOME "hermes-agent\venv\Scripts\hermes.exe"
if (-not (Test-Path $HERMES)) {
    Fail "hermes.exe not found at $HERMES (cannot auto-register)"
}
"Y" | & $HERMES mcp add credibility --command $BIN | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "hermes mcp add failed (exit $LASTEXITCODE)" }
Ok "MCP server registered"

# Verify
$LIST_OUT = & $HERMES mcp list 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "hermes mcp list failed (exit $LASTEXITCODE)"
}
if ($LIST_OUT -notmatch "credibility") {
    Fail "credibility MCP not found in hermes mcp list output"
}
Ok "MCP server visible in hermes mcp list"

Write-Host ""
Write-Host "[OK] credibility-mcp installed, scored, and registered." -ForegroundColor Green
Write-Host "    start a new Hermes session to load the tools (MCP tools load at startup)."
