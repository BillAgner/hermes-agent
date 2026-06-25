# uninstall_credibility_mcp.ps1
# Reverses install_credibility_mcp.ps1: uninstalls the package, removes the
# skills junction, and removes the MCP registration from config.yaml.

$ErrorActionPreference = "Stop"

$HERMES_HOME    = "C:\Data\Hermes_0.17.0"
$HERMES_VENV_PY = Join-Path $HERMES_HOME "hermes-agent\venv\Scripts\python.exe"
$SKILL_DST      = Join-Path $HERMES_HOME "skills\research\source-credibility"

function Step($msg) { Write-Host "[uninstall] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]        $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL]      $msg" -ForegroundColor Red; exit 1 }

Step "uninstall package"
& $HERMES_VENV_PY -m pip uninstall -y credibility-mcp --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip uninstall failed (exit $LASTEXITCODE)" }
Ok "package removed"

Step "remove skills junction"
if (Test-Path $SKILL_DST) {
    & cmd //c rmdir $SKILL_DST
    if ($LASTEXITCODE -ne 0) { Fail "junction removal failed (exit $LASTEXITCODE)" }
    Ok "junction removed"
} else {
    Ok "junction already absent"
}

Step "remove MCP registration"
$HERMES = Join-Path $HERMES_HOME "hermes-agent\venv\Scripts\hermes.exe"
if (Test-Path $HERMES) {
    & $HERMES mcp remove credibility | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "hermes mcp remove failed (exit $LASTEXITCODE)" }
    Ok "MCP registration removed"
}

Write-Host ""
Write-Host "[OK] credibility-mcp fully uninstalled." -ForegroundColor Green
