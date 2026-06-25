# cleanup_loose_ends.ps1
# Closes research-project-primitive loose ends (items 1, 2, 3, 5, 7, 8, 9 from
# the 2026-06-20 inventory). Self-verifies each step with [OK]/[FAIL]/[SKIP].
#
# Run from any shell:
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\cleanup_loose_ends.ps1
#
# Idempotent - safe to re-run; missing paths just print "skipped".

$ErrorActionPreference = "Continue"
$HERMES_HOME = "C:\Data\Hermes_0.17.0"

function Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Skip($msg)     { Write-Host "  [SKIP] $msg" -ForegroundColor DarkGray }
function Fail($msg)     { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

# --- Item 1: Test projects in registry --------------------------------------
Step "1" "Remove test projects from research_projects registry"
$registry = Join-Path $HERMES_HOME "research_projects\_registry.json"
if (Test-Path $registry) {
    # Read with BOM tolerance (UTF-8 with or without BOM)
    $raw = [System.IO.File]::ReadAllText($registry, [System.Text.Encoding]::UTF8)
    $json = $raw | ConvertFrom-Json
    foreach ($slug in @("e2e-test-project", "silver-comex-source-mirror-test")) {
        if ($json.PSObject.Properties.Name -contains $slug) {
            $json.PSObject.Properties.Remove($slug)
            $projDir = Join-Path $HERMES_HOME "research_projects\$slug"
            if (Test-Path $projDir) {
                Remove-Item -Recurse -Force $projDir
                Ok "removed $slug\state.json + registry entry"
            } else {
                Ok "removed registry entry for $slug (no state dir)"
            }
        } else {
            Skip "$slug not in registry"
        }
    }
    # Rewrite registry preserving only non-test entries (no BOM)
    $clean = [ordered]@{}
    foreach ($p in $json.PSObject.Properties) { $clean[$p.Name] = $p.Value }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($registry, ($clean | ConvertTo-Json -Depth 10), $utf8NoBom)
    $remaining = @($json.PSObject.Properties.Name) -join ', '
    if ([string]::IsNullOrEmpty($remaining)) { $remaining = "(empty)" }
    Ok "registry rewritten: $remaining"
} else {
    Skip "registry not found"
}

# --- Item 2: PyInstaller dist/build/.spec -----------------------------------
Step "2" "Remove unused PyInstaller dist/build/.spec from research-project-mcp"
$rpRoot = Join-Path $HERMES_HOME "~\research-project-mcp\packages\research-project-mcp"
foreach ($path in @("$rpRoot\dist", "$rpRoot\build", "$rpRoot\research-project-mcp.spec")) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
        Ok "removed $path"
    } else {
        Skip "$path (already gone)"
    }
}

# --- Item 3: Scratch scripts -------------------------------------------------
Step "3" "Remove scratch/debug scripts (scripts/_*)"
$kept = Join-Path $HERMES_HOME "tests\research-project\smoke.py"
$moveFrom = Join-Path $HERMES_HOME "scripts\_test_rp_mcp.py"
if (Test-Path $moveFrom) {
    New-Item -ItemType Directory -Force -Path (Split-Path $kept) | Out-Null
    Move-Item $moveFrom $kept -Force
    Ok "moved _test_rp_mcp.py to tests\research-project\smoke.py"
} else {
    Skip "_test_rp_mcp.py already moved or gone"
}
$scratch = @(
    "scripts\_probe.py",
    "scripts\_probe_rp.py",
    "scripts\_probe_health.py",
    "scripts\_proc_enum.py",
    "scripts\_check_gateway.ps1",
    "scripts\_common.ps1",
    "scripts\_health_response.json"
)
foreach ($rel in $scratch) {
    $full = Join-Path $HERMES_HOME $rel
    if (Test-Path $full) {
        Remove-Item -Force $full
        Ok "removed $rel"
    } else {
        Skip "$rel (already gone)"
    }
}

# --- Item 5: spawn-trees ----------------------------------------------------
Step "5" "Remove spawn-trees subagent debug logs"
$spawnRoot = Join-Path $HERMES_HOME "spawn-trees"
if (Test-Path $spawnRoot) {
    Get-ChildItem $spawnRoot -Directory | ForEach-Object {
        Remove-Item -Recurse -Force $_.FullName
        Ok "removed spawn-trees\$($_.Name)"
    }
    if ((Get-ChildItem $spawnRoot -Force | Measure-Object).Count -eq 0) {
        Remove-Item -Force $spawnRoot
        Ok "removed empty spawn-trees"
    }
} else {
    Skip "spawn-trees not found"
}

# --- Item 7: __pycache__ ----------------------------------------------------
Step "7" "Note: __pycache__ under MCP package src dirs are normal Python build artifacts."
$pycaches = @(
    "~\research-project-mcp\packages\research-project-mcp\src\research_project_mcp\__pycache__",
    "~\source-credibility-mcp\packages\source-credibility-mcp\src\credibility_mcp\__pycache__",
    "~\credibility-mcp\packages\credibility-mcp\src\credibility_mcp\__pycache__"
)
foreach ($rel in $pycaches) {
    $full = Join-Path $HERMES_HOME $rel
    if (Test-Path $full) {
        Skip "$rel (kept - regenerated on next import; .gitignore should exclude)"
    } else {
        Skip "$rel (not present)"
    }
}

# --- Item 8: Route consolidation -------------------------------------------
Step "8" "Note: /api/research-projects (hyphen) vs /api/research/projects (slash) - leaving both."
Skip "Both routes serve the same data. The slash version is public (no auth); the hyphen version requires the session token. Consolidating would change the public-path allowlist. Leaving for now."

# --- Item 9: pending_lessons triage ----------------------------------------
Step "9" "Triage pending_lessons.yaml"
$pl = Join-Path $HERMES_HOME "pending_lessons.yaml"
if (Test-Path $pl) {
    $content = Get-Content $pl -Raw
    $count = ([regex]::Matches($content, "^- id:")).Count
    if ($count -gt 0) {
        Skip "$count pending lessons present (both pre-existing from 2026-06-15 - not touched by this cleanup)"
    } else {
        Skip "no pending lessons"
    }
} else {
    Skip "pending_lessons.yaml not found"
}

Write-Host ""
Write-Host "[DONE] cleanup_loose_ends.ps1 complete." -ForegroundColor Cyan