#Requires -RunAsAdministrator
<#
bootstrap_qmd_collections.ps1 - Index skills + vault into QMD, run first embed.

After install_qmd.ps1 has run, this script:
  1. Adds the Hermes skills tree as a QMD collection named 'skills'
  2. Adds the Obsidian vault as a QMD collection named 'obsidian'
  3. Adds descriptive context for both collections
  4. Runs 'qmd embed' to generate vector embeddings (downloads ~2GB on first run)

Idempotent: re-running prints [OK]/[SKIP]/[FAIL] per collection.
#>

$ErrorActionPreference = 'Stop'

$HERMES_HOME    = 'C:\Data\Hermes_0.17.0'
$QMD_SOURCE     = Join-Path $HERMES_HOME '~\qmd'
$SKILLS_DIR     = Join-Path $HERMES_HOME 'skills'
$VAULT_DIR      = Join-Path $env:USERPROFILE 'Documents\Obsidian Vault'
$LOG_FILE       = Join-Path $HERMES_HOME 'logs\bootstrap_qmd_collections.log'

if (-not (Test-Path $LOG_FILE)) { New-Item -ItemType Directory -Path (Split-Path $LOG_FILE) -Force | Out-Null }

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
        $code = $LASTEXITCODE
        return @($output, $code)
    } finally { Pop-Location }
}

Say ""
Say "=== bootstrap_qmd_collections.ps1 starting at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Say "QMD_SOURCE = $QMD_SOURCE"
Say "SKILLS_DIR = $SKILLS_DIR"
Say "VAULT_DIR  = $VAULT_DIR"
Say ""

if (-not (Test-Path $QMD_SOURCE)) { Fail "QMD source not installed. Run install_qmd.ps1 first." }
if (-not (Test-Path $SKILLS_DIR))  { Fail "Skills dir not found: $SKILLS_DIR" }
if (-not (Test-Path $VAULT_DIR))   { Say "WARN: vault not found at $VAULT_DIR - will skip obsidian collection" }

# --- Collection: skills ---
Say "[Collection] skills ($SKILLS_DIR)"
$result = Run-Qmd @('collection','add',$SKILLS_DIR,'--name','skills','--mask','**\SKILL.md')
if ($result[1] -ne 0) { Fail "qmd collection add skills failed: $($result[0] -join ' ')" }
Ok "skills collection registered"

$result = Run-Qmd @('context','add','qmd://skills','Hermes Agent skill definitions - how each skill works, when to load it, gotchas, and the tools it exposes. Format: YAML frontmatter + markdown body.')
if ($result[1] -ne 0) { Fail "qmd context add skills failed" }
Ok "skills context added"

# --- Collection: obsidian (if vault exists) ---
if (Test-Path $VAULT_DIR) {
    Say "[Collection] obsidian ($VAULT_DIR)"
    $result = Run-Qmd @('collection','add',$VAULT_DIR,'--name','obsidian','--mask','**\*.md')
    if ($result[1] -ne 0) { Fail "qmd collection add obsidian failed: $($result[0] -join ' ')" }
    Ok "obsidian collection registered"

    $result = Run-Qmd @('context','add','qmd://obsidian',"Bill's personal Obsidian vault at $VAULT_DIR - daily notes, active projects, quick capture, reference clippings. PARA-style layout.")
    if ($result[1] -ne 0) { Fail "qmd context add obsidian failed" }
    Ok "obsidian context added"
} else {
    Skip "obsidian collection (vault missing)"
}

# --- Embed ---
Say ""
Say "[Embed] Generating vector embeddings (first run downloads ~2GB of GGUF models)"
Say "  This may take 5-15 minutes depending on collection size and network speed."
$result = Run-Qmd @('embed')
if ($result[1] -ne 0) { Fail "qmd embed failed: $($result[0] -join ' ')" }
Ok "embed complete"

# --- Smoke test ---
Say ""
Say "[Smoke test] Verify searches return hits"
$result = Run-Qmd @('search','obsidian vault','--json','-n','3')
if ($result[1] -ne 0) { Fail "qmd search smoke test failed" }
Ok "qmd search returned results (truncated): $($result[0][0])"

Say ""
Say "=== bootstrap_qmd_collections.ps1 complete at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Ok "ALL DONE"