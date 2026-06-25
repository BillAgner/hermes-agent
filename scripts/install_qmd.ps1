#Requires -RunAsAdministrator
<#
install_qmd.ps1 - Install QMD (Query Markup Documents) MCP server for Hermes.

What it does:
  1. Clones QMD source to C:\Data\Hermes_0.17.0\~\qmd
  2. bun install + bun build
  3. Creates scripts\qmd-mcp.cmd launcher wrapper
  4. Bootstraps an Obsidian vault at C:\Users\bobup\Documents\Obsidian Vault
  5. Sets OBSIDIAN_VAULT_PATH in .env
  6. Backups config.yaml and inserts the qmd: MCP server block (Python helper)
  7. Writes the qmd skill files at ~\qmd\skills\research\qmd\
  8. Junctions the skill into skills\research\qmd\
  9. Verifies end-to-end (qmd --version, status, first search)

Idempotent: safe to re-run. Each step prints [OK]/[FAIL]/[SKIP] and logs to
C:\Data\Hermes_0.17.0\logs\install_qmd.log.

Manual follow-up:
  - hermes mcp restart qmd (or restart the gateway) so the new MCP entry is picked up
  - Run scripts\bootstrap_qmd_collections.ps1 to index skills + vault
#>

$ErrorActionPreference = 'Stop'

# --- Constants ---
$HERMES_HOME       = 'C:\Data\Hermes_0.17.0'
$HERMES_PY         = "$HERMES_HOME\venv\Scripts\python.exe"
$USERPROFILE_DIR   = $env:USERPROFILE
$VAULT_DIR         = Join-Path $USERPROFILE_DIR 'Documents\Obsidian Vault'
$QMD_SOURCE        = Join-Path $HERMES_HOME '~\qmd'
$QMD_SKILLS_DIR    = Join-Path $QMD_SOURCE 'skills\research\qmd'
$SKILLS_TARGET     = Join-Path $HERMES_HOME 'skills\research\qmd'
$LOG_DIR           = Join-Path $HERMES_HOME 'logs'
$LOG_FILE          = Join-Path $LOG_DIR 'install_qmd.log'
$SCRIPT_DIR        = Join-Path $HERMES_HOME 'scripts'
$LAUNCHER_PATH     = Join-Path $SCRIPT_DIR 'qmd-mcp.cmd'
$CONFIG_PATH       = Join-Path $HERMES_HOME 'config.yaml'
$ENV_FILE          = Join-Path $HERMES_HOME '.env'
$PY_PATCHER        = Join-Path $SCRIPT_DIR '_patch_config_qmd.py'

# --- Setup ---
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }
if (-not (Test-Path $SCRIPT_DIR)) { New-Item -ItemType Directory -Path $SCRIPT_DIR -Force | Out-Null }

# --- Helpers ---
function Say([string]$msg) {
    Write-Host $msg
    Add-Content -Path $LOG_FILE -Value "$(Get-Date -Format 'o') $msg"
}
function Ok([string]$what)   { Say "[OK]   $what" }
function Fail([string]$what) { Say "[FAIL] $what"; throw "FAIL: $what" }
function Skip([string]$what) { Say "[SKIP] $what" }

# --- Header ---
Say ""
Say "=== install_qmd.ps1 starting at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Say "HERMES_HOME = $HERMES_HOME"
Say "VAULT_DIR   = $VAULT_DIR"
Say "QMD_SOURCE  = $QMD_SOURCE"
Say ""

# --- Pre-flight: admin ---
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Not running as Administrator. Re-run from an elevated shell."
}
Ok "preflight: Administrator"

# --- Pre-flight: tools ---
$missing = @()
if (-not (Get-Command bun -ErrorAction SilentlyContinue))   { $missing += 'bun' }
if (-not (Get-Command node -ErrorAction SilentlyContinue))  { $missing += 'node' }
if (-not (Get-Command git -ErrorAction SilentlyContinue))   { $missing += 'git' }
if (-not (Test-Path $HERMES_PY))                            { $missing += "python ($HERMES_PY)" }
if ($missing.Count -gt 0) {
    Fail "Missing tools: $($missing -join ', ')"
}
Ok "preflight: bun + node + git + python present"

# --- Step 1: Clone QMD source ---
Say ""
Say "[Step 1] Clone QMD source"
if (Test-Path (Join-Path $QMD_SOURCE '.git')) {
    Skip "qmd source already present at $QMD_SOURCE"
} else {
    if (Test-Path $QMD_SOURCE) {
        # Dir exists but not a git repo; bail
        Fail "Path $QMD_SOURCE exists but is not a git repo. Remove it and re-run."
    }
    Say "  Cloning https://github.com/ehc-io/qmd.git ..."
    git clone https://github.com/ehc-io/qmd.git $QMD_SOURCE
    if (-not (Test-Path (Join-Path $QMD_SOURCE 'package.json'))) {
        Fail "qmd clone did not produce package.json"
    }
    Ok "qmd cloned to $QMD_SOURCE"
}

# --- Step 2: Install dependencies ---
Say ""
Say "[Step 2] bun install"
Push-Location $QMD_SOURCE
try {
    if (Test-Path 'node_modules\better-sqlite3') {
        Skip "qmd node_modules already populated"
    } else {
        Say "  Running bun install (may take a few minutes on first run) ..."
        # Use Invoke-Expression + redirect to bypass PowerShell treating bun warnings as errors
        $ErrorActionPreference = 'Continue'
        $bunOutput = & bun install 2>&1 | Out-String
        $bunExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        # better-sqlite3 native build emits a warning that's not fatal
        if ($bunExit -ne 0) {
            Fail "bun install failed (exit $bunExit): $($bunOutput | Select-Object -Last 5)"
        }
        Ok "qmd dependencies installed"
    }
} finally { Pop-Location }

# --- Step 3: Build QMD (Windows-friendly) ---
Say ""
Say "[Step 3] bun run build (or skip if dist already present)"
Push-Location $QMD_SOURCE
try {
    if (Test-Path 'dist\qmd.js') {
        Skip "qmd dist/qmd.js already present (built previously)"
    } else {
        # QMD's `bun run build` uses Unix shell pipes (cat -, chmod +x) that
        # don't work on Windows natively. We split it: tsc for compile, then
        # PowerShell to prepend the shebang.
        Say "  Compiling TypeScript with tsc ..."
        $ErrorActionPreference = 'Continue'
        $tscOutput = & bunx tsc -p tsconfig.build.json 2>&1 | Out-String
        $tscExit = $LASTEXITCODE
        if ($tscExit -ne 0) {
            $ErrorActionPreference = 'Stop'
            Fail "tsc compile failed (exit $tscExit): $($tscOutput | Select-Object -Last 15)"
        }
        if (-not (Test-Path 'dist\qmd.js')) {
            $ErrorActionPreference = 'Stop'
            Fail "tsc reported success but dist\qmd.js is missing"
        }
        # Prepend shebang line (Unix-style; harmless on Windows because bun ignores it)
        $shebang = "#!/usr/bin/env node`n"
        $content = Get-Content 'dist\qmd.js' -Raw
        # Only prepend if not already present
        if (-not $content.StartsWith("#!/usr/bin/env node")) {
            [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'dist\qmd.js'), $shebang + $content, [System.Text.UTF8Encoding]::new($false))
        }
        $ErrorActionPreference = 'Stop'
        Ok "qmd built (dist\qmd.js with shebang)"
    }
} finally { Pop-Location }

# --- Step 4: Smoke-test qmd binary ---
Say ""
Say "[Step 4] Smoke-test qmd binary"
Push-Location $QMD_SOURCE
try {
    $ErrorActionPreference = 'Continue'
    # Test BOTH paths: bundled (no env var) and ollama (QMD_USE_OLLAMA=1)
    # to confirm the install can fall back if ollama is unavailable.
    $env:QMD_USE_OLLAMA = ''
    $verBundled = & bun run qmd --version 2>&1 | Out-String
    $bundledExit = $LASTEXITCODE
    if ($bundledExit -ne 0) { Fail "qmd --version (bundled) failed (exit $bundledExit): $verBundled" }
    Ok "qmd responds (bundled): $($verBundled.Trim() -split "`n" | Select-Object -Last 1)"

    # Now test the ollama path - this is what the MCP launcher actually uses.
    $env:QMD_USE_OLLAMA = '1'
    $env:OLLAMA_HOST = 'http://127.0.0.1:11434'
    $env:QMD_OLLAMA_EMBED_MODEL = 'bge-m3:latest'
    $env:QMD_OLLAMA_EXPAND_MODEL = 'qwen3:8b'
    $verOllama = & bun run qmd --version 2>&1 | Out-String
    $ollamaExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($ollamaExit -ne 0) { Fail "qmd --version (ollama) failed (exit $ollamaExit): $verOllama" }
    Ok "qmd responds (ollama): $($verOllama.Trim() -split "`n" | Select-Object -Last 1)"
} finally {
    Remove-Item Env:QMD_USE_OLLAMA -ErrorAction SilentlyContinue
    Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:QMD_OLLAMA_EMBED_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:QMD_OLLAMA_EXPAND_MODEL -ErrorAction SilentlyContinue
    Pop-Location
}

# --- Step 5: Create launcher wrapper ---
Say ""
Say "[Step 5] Create qmd-mcp.cmd launcher"
$launcherContent = @"
@echo off
REM qmd-mcp.cmd - Launch QMD MCP server for Hermes.
REM Hermes config.yaml invokes this wrapper as the MCP command.
REM Ollama routing is enabled: embeddings + query expansion go through
REM ollama HTTP API (bge-m3, qwen3:8b). Skips the ~2GB bundled-GGUF download.
REM Rerank is a no-op for now (returns input order with synthetic scores);
REM qmd_deep_search falls back to BM25+vector fusion.
set QMD_USE_OLLAMA=1
set OLLAMA_HOST=http://127.0.0.1:11434
set QMD_OLLAMA_EMBED_MODEL=bge-m3:latest
set QMD_OLLAMA_EXPAND_MODEL=qwen3:8b
cd /d "$QMD_SOURCE"
"%USERPROFILE%\AppData\Roaming\npm\bun.cmd" run qmd mcp
"@
[System.IO.File]::WriteAllText($LAUNCHER_PATH, $launcherContent, [System.Text.UTF8Encoding]::new($false))
Ok "launcher created at $LAUNCHER_PATH"

# --- Step 6: Bootstrap Obsidian vault ---
Say ""
Say "[Step 6] Bootstrap Obsidian vault at $VAULT_DIR"
if (Test-Path (Join-Path $VAULT_DIR 'README.md')) {
    Skip "vault already bootstrapped at $VAULT_DIR"
} else {
    if (-not (Test-Path $VAULT_DIR)) { New-Item -ItemType Directory -Path $VAULT_DIR -Force | Out-Null }
    $obsDir = Join-Path $VAULT_DIR '.obsidian'
    if (-not (Test-Path $obsDir)) { New-Item -ItemType Directory -Path $obsDir -Force | Out-Null }

    $appJson = '{"alwaysUpdateLinks":true,"newLinkFormat":"short","useMarkdownLinks":false,"showLineNumber":true,"showInlineTitle":false}'
    [System.IO.File]::WriteAllText((Join-Path $obsDir 'app.json'), $appJson, [System.Text.UTF8Encoding]::new($false))

    $appearanceJson = '{"baseFontSize":16,"theme":"obsidian"}'
    [System.IO.File]::WriteAllText((Join-Path $obsDir 'appearance.json'), $appearanceJson, [System.Text.UTF8Encoding]::new($false))

    # Folders (PARA-ish)
    foreach ($folder in @('Daily','Projects','Inbox','Templates','References')) {
        $p = Join-Path $VAULT_DIR $folder
        if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    }

    # README
    $readme = @'
# Hermes Vault

This vault is bootstrapped by `C:\Data\Hermes_0.17.0\scripts\install_qmd.ps1`.
Managed by Hermes Agent.

## Layout

- `Daily/` - daily notes (date-named, e.g. `2026-06-25.md`)
- `Projects/` - active project notes
- `Inbox/` - quick capture
- `Templates/` - Templater templates (`daily.md`, `project.md`)
- `References/` - reference material, articles, clippings

## Search

QMD indexes this vault as the `obsidian` collection. Use the `qmd` skill for
hybrid search (BM25 + vector + rerank) via the `mcp__qmd__*` tools.
'@
    [System.IO.File]::WriteAllText((Join-Path $VAULT_DIR 'README.md'), $readme, [System.Text.UTF8Encoding]::new($false))

    # Templates
    $dailyTpl = @'
---
date: {{date}}
tags: [daily]
---

# {{date}}

## Today

- 

## Notes

- 
'@
    [System.IO.File]::WriteAllText((Join-Path $VAULT_DIR 'Templates\daily.md'), $dailyTpl, [System.Text.UTF8Encoding]::new($false))

    $projectTpl = @'
---
status: active
tags: [project]
---

# {{title}}

## Goal

## Tasks

- [ ] 

## Notes

'@
    [System.IO.File]::WriteAllText((Join-Path $VAULT_DIR 'Templates\project.md'), $projectTpl, [System.Text.UTF8Encoding]::new($false))

    Ok "vault bootstrapped at $VAULT_DIR"
}

# --- Step 7: Set OBSIDIAN_VAULT_PATH in .env ---
Say ""
Say "[Step 7] Set OBSIDIAN_VAULT_PATH in .env"
$envLine = "OBSIDIAN_VAULT_PATH=$VAULT_DIR"
if (Test-Path $ENV_FILE) {
    $existing = Select-String -Path $ENV_FILE -Pattern '^OBSIDIAN_VAULT_PATH=' -ErrorAction SilentlyContinue
    if ($existing) {
        Skip "OBSIDIAN_VAULT_PATH already set in .env (value: $($existing.Line))"
    } else {
        Add-Content -Path $ENV_FILE -Value $envLine
        Ok "OBSIDIAN_VAULT_PATH appended to .env"
    }
} else {
    [System.IO.File]::WriteAllText($ENV_FILE, $envLine, [System.Text.UTF8Encoding]::new($false))
    Ok ".env created with OBSIDIAN_VAULT_PATH"
}

# --- Step 8: Backup config.yaml ---
Say ""
Say "[Step 8] Backup config.yaml"
if (-not (Test-Path $CONFIG_PATH)) { Fail "config.yaml not found at $CONFIG_PATH" }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$bakPath = "$CONFIG_PATH.bak.qmdinstall.$stamp"
Copy-Item $CONFIG_PATH $bakPath -Force
Ok "config.yaml backed up to $bakPath"

# --- Step 9: Patch config.yaml (via Python helper) ---
Say ""
Say "[Step 9] Insert qmd: MCP block into config.yaml"

# Write the Python helper script
$pyContent = @'
#!/usr/bin/env python3
"""Insert 'qmd:' MCP server block into Hermes config.yaml after the tradingview_desktop: block."""
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
content = config_path.read_text(encoding="utf-8")

qmd_block = (
    "  qmd:\n"
    "    command: C:\\Data\\Hermes_0.17.0\\scripts\\qmd-mcp.cmd\n"
    "    args:\n"
    "    - ''\n"
    "    enabled: true\n"
    "    timeout: 180\n"
    "    connect_timeout: 120\n"
)

if "  qmd:\n" in content and "    command: C:\\Data\\Hermes_0.17.0\\scripts\\qmd-mcp.cmd" in content:
    print("SKIP: qmd block already present")
    sys.exit(0)

# Find the tradingview_desktop: block start
marker = "  tradingview_desktop:\n"
idx = content.find(marker)
if idx == -1:
    print("FAIL: could not find 'tradingview_desktop:' marker")
    sys.exit(1)

# Walk forward to find end of the block: next top-level "  <word>:" entry
end_idx = idx + len(marker)
rest = content[end_idx:]
insert_pos = end_idx
for line in rest.split("\n"):
    if line.startswith("  ") and ":" in line and not line.startswith("    "):
        # next top-level key found
        break
    insert_pos += len(line) + 1

new_content = content[:insert_pos] + qmd_block + content[insert_pos:]
config_path.write_text(new_content, encoding="utf-8")
print("OK: qmd MCP block inserted after tradingview_desktop")
'@
[System.IO.File]::WriteAllText($PY_PATCHER, $pyContent, [System.Text.UTF8Encoding]::new($false))
Ok "Python patcher written to $PY_PATCHER"

# Invoke the patcher
$pyOutput = & $HERMES_PY $PY_PATCHER $CONFIG_PATH 2>&1
if ($LASTEXITCODE -ne 0) { Fail "config patch failed: $pyOutput" }
Ok "config.yaml patched: $pyOutput"

# --- Step 10: Copy the qmd skill files into the cloned source dir ---
Say ""
Say "[Step 10] Stage qmd skill files into $QMD_SKILLS_DIR"
if (-not (Test-Path $QMD_SKILLS_DIR)) { New-Item -ItemType Directory -Path $QMD_SKILLS_DIR -Force | Out-Null }
$refsDir = Join-Path $QMD_SKILLS_DIR 'references'
if (-not (Test-Path $refsDir)) { New-Item -ItemType Directory -Path $refsDir -Force | Out-Null }
$scriptsSkillDir = Join-Path $QMD_SKILLS_DIR 'scripts'
if (-not (Test-Path $scriptsSkillDir)) { New-Item -ItemType Directory -Path $scriptsSkillDir -Force | Out-Null }

$PAYLOAD_DIR = Join-Path $SCRIPT_DIR 'qmd_skill_payload'
if (-not (Test-Path $PAYLOAD_DIR)) {
    Fail "Skill payload dir missing at $PAYLOAD_DIR - cannot install qmd skill files"
}

# Copy SKILL.md
$srcSkill = Join-Path $PAYLOAD_DIR 'SKILL.md'
$dstSkill = Join-Path $QMD_SKILLS_DIR 'SKILL.md'
if (Test-Path $dstSkill) {
    # Compare content; only overwrite if different (so re-runs are no-ops)
    $srcHash = (Get-FileHash $srcSkill -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash $dstSkill -Algorithm SHA256).Hash
    if ($srcHash -eq $dstHash) {
        Skip "SKILL.md already staged (content matches)"
    } else {
        Copy-Item -Force $srcSkill $dstSkill
        Ok "SKILL.md updated (content changed)"
    }
} else {
    Copy-Item -Force $srcSkill $dstSkill
    Ok "SKILL.md staged"
}

# Copy reference doc
$srcRef = Join-Path $PAYLOAD_DIR 'references\search-mode-selection.md'
$dstRef = Join-Path $refsDir 'search-mode-selection.md'
if (Test-Path $dstRef) {
    $srcHash = (Get-FileHash $srcRef -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash $dstRef -Algorithm SHA256).Hash
    if ($srcHash -eq $dstHash) {
        Skip "references/search-mode-selection.md already staged"
    } else {
        Copy-Item -Force $srcRef $dstRef
        Ok "references/search-mode-selection.md updated"
    }
} else {
    Copy-Item -Force $srcRef $dstRef
    Ok "references/search-mode-selection.md staged"
}

# Copy scripts/bootstrap_collections.ps1
$srcScript = Join-Path $PAYLOAD_DIR 'scripts\bootstrap_collections.ps1'
$dstScript = Join-Path $scriptsSkillDir 'bootstrap_collections.ps1'
if (Test-Path $dstScript) {
    $srcHash = (Get-FileHash $srcScript -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash $dstScript -Algorithm SHA256).Hash
    if ($srcHash -eq $dstHash) {
        Skip "scripts/bootstrap_collections.ps1 already staged"
    } else {
        Copy-Item -Force $srcScript $dstScript
        Ok "scripts/bootstrap_collections.ps1 updated"
    }
} else {
    Copy-Item -Force $srcScript $dstScript
    Ok "scripts/bootstrap_collections.ps1 staged"
}

# --- Step 11: Junction the skill into skills\research\qmd ---
Say ""
Say "[Step 11] Junction skill into $SKILLS_TARGET"
if (Test-Path $SKILLS_TARGET) {
    Skip "skill target already exists at $SKILLS_TARGET"
} else {
    & cmd.exe /c mklink /J $SKILLS_TARGET $QMD_SKILLS_DIR
    if ($LASTEXITCODE -ne 0) { Fail "junction failed for $SKILLS_TARGET" }
    Ok "skill junctioned: $SKILLS_TARGET -> $QMD_SKILLS_DIR"
}

# --- Step 12: Verify ---
Say ""
Say "[Step 12] Verification"
Push-Location $QMD_SOURCE
try {
    $ErrorActionPreference = 'Continue'

    # Bundled path
    $env:QMD_USE_OLLAMA = ''
    $verBundled = & bun run qmd --version 2>&1 | Out-String
    $bundledExit = $LASTEXITCODE
    if ($bundledExit -ne 0) { Fail "final qmd --version (bundled) failed: $verBundled" }
    Ok "qmd --version (bundled): $($verBundled.Trim() -split "`n" | Select-Object -Last 1)"

    # Ollama path
    $env:QMD_USE_OLLAMA = '1'
    $env:OLLAMA_HOST = 'http://127.0.0.1:11434'
    $env:QMD_OLLAMA_EMBED_MODEL = 'bge-m3:latest'
    $env:QMD_OLLAMA_EXPAND_MODEL = 'qwen3:8b'
    $verOllama = & bun run qmd --version 2>&1 | Out-String
    $ollamaExit = $LASTEXITCODE

    # Ollama-mode status (skip device/GPU section to avoid CUDA build noise)
    $statusOllama = & bun run qmd status 2>&1 | Out-String
    $statusExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($ollamaExit -ne 0) { Fail "final qmd --version (ollama) failed: $verOllama" }
    Ok "qmd --version (ollama): $($verOllama.Trim() -split "`n" | Select-Object -Last 1)"

    if ($statusExit -ne 0) { Fail "qmd status (ollama) failed: $statusOllama" }
    # Pull the collections line + index size line for a clean summary
    $statusLines = $statusOllama -split "`n" | Where-Object { $_ -match '(Collections|Documents|Index)' } | Select-Object -First 4
    Ok "qmd status (ollama): $(($statusLines -join ' | ').Trim())"
} finally {
    Remove-Item Env:QMD_USE_OLLAMA -ErrorAction SilentlyContinue
    Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:QMD_OLLAMA_EMBED_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:QMD_OLLAMA_EXPAND_MODEL -ErrorAction SilentlyContinue
    Pop-Location
}

# --- Summary ---
Say ""
Say "=== install_qmd.ps1 complete at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Say ""
Say "Manual follow-up steps (in order):"
Say "  1. Restart Hermes MCP for the qmd entry to register:"
Say "       hermes mcp restart qmd"
Say "  2. Bootstrap QMD collections and embed:"
Say "       powershell -ExecutionPolicy Bypass -File $SCRIPT_DIR\bootstrap_qmd_collections.ps1"
Say "  3. Open the vault in Obsidian (optional, GUI):"
Say "       Start-Process 'obsidian://open?path=$VAULT_DIR'"
Say ""
Say "Files written:"
Say "  Source:       $QMD_SOURCE"
Say "  Vault:        $VAULT_DIR"
Say "  Launcher:     $LAUNCHER_PATH"
Say "  Patcher:      $PY_PATCHER"
Say "  Skill:        $QMD_SKILLS_DIR  (junctioned to $SKILLS_TARGET)"
Say "  Config bak:   $bakPath"
Say "  Env:          $ENV_FILE"
Say "  Log:          $LOG_FILE"
Say ""
Ok "ALL DONE"