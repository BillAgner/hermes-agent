# PowerShell admin-script template

When the user needs an admin operation that requires UAC, scheduled task registration, or service control, the deliverable is a single PowerShell script on disk. Not a multi-step recipe. The script should self-verify and end with a clear `[OK]/[FAIL]/[SKIP]` line per operation.

Template:

```powershell
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    <one-line summary>

.DESCRIPTION
    <longer description of what this does and why>

.NOTES
    Idempotent: safe to re-run.
    Verifies each step and prints [OK]/[FAIL]/[SKIP] per item.
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step {
    param([string]$Status, [string]$Message)
    $color = switch ($Status) {
        'OK'   { 'Green' }
        'FAIL' { 'Red' }
        'SKIP' { 'Yellow' }
        default { 'White' }
    }
    Write-Host "[$Status] $Message" -ForegroundColor $color
}

# ── Locate paths ──────────────────────────────────────────────────────
$HermesHome = $env:HERMES_HOME
if (-not $HermesHome) { $HermesHome = Join-Path $env:USERPROFILE '.hermes' }
$VenvPython = Join-Path $HermesHome 'hermes-agent\venv\Scripts\python.exe'
$ConfigFile = Join-Path $HermesHome 'config.yaml'

# ── Step 1: verify preconditions ─────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Step FAIL "venv python missing: $VenvPython"
    exit 1
}
Write-Step OK "venv python present"

# ── Step 2: do the work ──────────────────────────────────────────────
try {
    # example: register a scheduled task
    $taskName = 'HermesWatchdog'
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Step SKIP "scheduled task already registered: $taskName"
    } else {
        $action = New-ScheduledTaskAction `
            -Execute $VenvPython `
            -Argument '-m hermes_bootstrap scripts\hermes_watchdog.py'
        $trigger = New-ScheduledTaskTrigger -AtStartup
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Description 'Hermes self-healing watchdog' `
            -RunLevel Highest | Out-Null
        Write-Step OK "registered scheduled task: $taskName"
    }
} catch {
    Write-Step FAIL "scheduled task registration: $_"
    exit 1
}

# ── Step 3: verify outcome ───────────────────────────────────────────
try {
    $task = Get-ScheduledTask -TaskName 'HermesWatchdog'
    if ($task.State -eq 'Ready') {
        Write-Step OK "watchdog task is Ready"
    } else {
        Write-Step FAIL "watchdog task state: $($task.State)"
        exit 1
    }
} catch {
    Write-Step FAIL "could not query watchdog task: $_"
    exit 1
}

Write-Host ''
Write-Host 'Done. Re-run safely to verify state.'
```

## Rules

- **One file on disk**, one path returned to the user. Never a multi-step recipe.
- **`[OK]/[FAIL]/[SKIP]` per item**, color-coded. The user can scan the output.
- **Idempotent** — re-running produces the same end state. Use `-ErrorAction SilentlyContinue` when checking preconditions; use `try/catch` only when the operation itself can fail.
- **Verify after doing** — after each mutating operation, re-read the state and assert it's correct.
- **End with `exit 1` on any FAIL** so the script is safe to chain in a larger pipeline.
- **No interactive prompts** — if a step needs admin, the script should require `#Requires -RunAsAdministrator` and fail loudly if not.

## Common traps

- **UTF-8 + non-ASCII in `.ps1` files silently aborts the parser.** Symbols like `✓`, `→`, `—` cause "The term 'X' is not recognized" pointing at the wrong character. Fix: stick to ASCII (`[OK]`, `->`, `--`) OR save with UTF-8 BOM via `[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($true))`.
- **Regex patterns in double-quoted strings** (`$config -replace '^(\s*)port:\s*\d+', ...`) get parsed as commands — `\s`, `\d`, parenthesized content after a backtick get interpreted as command tokens. Fix: store the pattern in a variable first (`$pattern = '^(\s*)port:\s*\d+'`) or use `[System.Text.RegularExpressions.Regex]::Replace`.
- **`Set-Content -Encoding UTF8` does NOT add BOM.** Use the .NET API above if you need BOM, or write the file as ASCII.