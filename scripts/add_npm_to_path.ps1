# add_npm_to_path.ps1
# Adds %AppData%\npm to the system PATH so npm-installed CLI tools (pnpm, etc.)
# resolve in new terminal sessions. Idempotent: detects existing entry and exits
# [OK] without modification.
#
# By default writes to the USER PATH (HKCU\Environment, no admin needed).
# Use -System to write to the MACHINE PATH (HKLM, self-elevates to UAC).
# Use -Force to add even if the expanded form already exists under a different
# spelling (e.g. an old absolute path that should be replaced).
#
# Usage (default, user PATH, no admin):
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\add_npm_to_path.ps1
#
# Machine-wide (self-elevates):
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\add_npm_to_path.ps1 -System
#
# The PATH change takes effect for NEW processes. Existing terminal windows
# and the running Hermes session will not see it until they restart.

[CmdletBinding()]
param(
    [switch]$System,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$Entry = '%AppData%\npm'   # canonical form — expands at lookup time, survives rename of the user profile
$Target = if ($System) { 'Machine' } else { 'User' }

# Read current PATH (as a list, not a string — [Environment] joins on ';')
$current = [Environment]::GetEnvironmentVariable('Path', $Target)
if (-not $current) {
    if ($System) {
        Write-Host "[FAIL] could not read Machine PATH (admin required?)"
        exit 1
    }
    $current = ''
}

# Normalize: split on ';' (PATH separator), trim, drop empties
$entries = $current -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }

# Detect existing entry. Match both the canonical (%AppData%\npm) and the
# fully-expanded form (C:\Users\<user>\AppData\Roaming\npm) — covers old installs.
$userProfile = $env:USERPROFILE
$expanded = [Environment]::ExpandEnvironmentVariables($Entry)

$hasCanonical = $entries -contains $Entry
$hasExpanded  = $entries -contains $expanded

if (($hasCanonical -or $hasExpanded) -and -not $Force) {
    $which = if ($hasCanonical) { $Entry } else { $expanded }
    Write-Host "[OK] PATH already contains $which ($Target scope)"
    Write-Host "     (use -Force to add a duplicate or switch to the canonical form)"
    exit 0
}

# If the expanded form is present but not the canonical form, swap it for the
# canonical form (more portable across profile renames and easier to spot).
if ($hasExpanded -and -not $hasCanonical -and $Force) {
    $entries = $entries | Where-Object { $_ -ne $expanded }
    Write-Host "     swapping expanded form for canonical $Entry"
}

# Append canonical entry (env var expansion happens at lookup, not at write)
$entries += $Entry

# Re-join and write back
$newPath = ($entries -join ';')
try {
    [Environment]::SetEnvironmentVariable('Path', $newPath, $Target)
} catch [System.Security.SecurityException] {
    Write-Host "[FAIL] write to $Target PATH denied: $($_.Exception.Message)"
    Write-Host "     re-run with -System from a non-elevated shell and accept the UAC prompt"
    exit 1
} catch {
    Write-Host "[FAIL] $($_.Exception.Message)"
    exit 1
}

# Self-verify: read back and confirm
$verify = [Environment]::GetEnvironmentVariable('Path', $Target)
if ($verify -notmatch [regex]::Escape($Entry)) {
    Write-Host "[FAIL] post-write verification could not find $Entry in $Target PATH"
    Write-Host "       read-back value: $verify"
    exit 1
}

Write-Host "[OK] added $Entry to $Target PATH"
Write-Host "     new PATH entries: $($entries.Count) total"
Write-Host "     verification: $Entry is present in the read-back value"
Write-Host "     note: existing terminal windows and the running Hermes session still see the OLD PATH."
Write-Host "           open a new terminal (or restart Hermes) to pick up the change."
exit 0
