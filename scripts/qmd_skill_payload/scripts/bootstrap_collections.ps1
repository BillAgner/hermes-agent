#Requires -RunAsAdministrator
<#
bootstrap_collections.ps1 - Add standard collections to an existing QMD install.

Idempotent. Safe to re-run. Each step prints [OK]/[SKIP]/[FAIL].

This is the per-skill version (lives with the qmd skill). The system-wide
equivalent lives at C:\Data\Hermes_0.17.0\scripts\bootstrap_qmd_collections.ps1.
#>

$ErrorActionPreference = 'Stop'

$HERMES_HOME = 'C:\Data\Hermes_0.17.0'
$QMD_SOURCE  = Join-Path $HERMES_HOME '~\qmd'
$SKILLS_DIR  = Join-Path $HERMES_HOME 'skills'
$VAULT_DIR   = Join-Path $env:USERPROFILE 'Documents\Obsidian Vault'
$LOG_FILE    = Join-Path $HERMES_HOME 'logs\bootstrap_qmd_collections.log'

function Say([string]$msg) {
    Write-Host $msg
    Add-Content -Path $LOG_FILE -Value "$(Get-Date -Format 'o') $msg"
}
function Ok([string]$what)   { Say "[OK]   $what" }
function Fail([string]$what) { Say "[FAIL] $what"; throw "FAIL: $what" }
function Skip([string]$what) { Say "[SKIP] $what" }

function Run-Qmd([string[]]$args) {
    Push-Location $QMD_SOURCE
    try {
        $output = & bun run qmd @args 2>&1
        return @($output, $LASTEXITCODE)
    } finally { Pop-Location }
}

Say "=== bootstrap_collections.ps1 starting ==="

if (-not (Test-Path $QMD_SOURCE)) { Fail "QMD source missing at $QMD_SOURCE" }

# skills collection
$result = Run-Qmd @('collection','add',$SKILLS_DIR,'--name','skills','--mask','**\SKILL.md')
if ($result[1] -ne 0) { Say "skills collection may already exist: $($result[0] -join ' ')" } else { Ok "skills collection added" }

$result = Run-Qmd @('context','add','qmd://skills','Hermes Agent skill definitions - format, content, triggers.')
if ($result[1] -eq 0) { Ok "skills context added" }

# obsidian collection (if vault present)
if (Test-Path $VAULT_DIR) {
    $result = Run-Qmd @('collection','add',$VAULT_DIR,'--name','obsidian','--mask','**\*.md')
    if ($result[1] -ne 0) { Say "obsidian collection may already exist: $($result[0] -join ' ')" } else { Ok "obsidian collection added" }

    $result = Run-Qmd @('context','add','qmd://obsidian',"Bill's personal Obsidian vault - PARA layout, daily notes, projects.")
    if ($result[1] -eq 0) { Ok "obsidian context added" }
} else {
    Skip "obsidian collection (vault missing at $VAULT_DIR)"
}

# Embed
$result = Run-Qmd @('embed')
if ($result[1] -ne 0) { Fail "embed failed: $($result[0] -join ' ')" }
Ok "embed complete"

Ok "ALL DONE"