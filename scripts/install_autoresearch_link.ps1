# install_autoresearch_link.ps1
# Creates C:\Data\Hermes_0.17.0\skills\research\autoresearch\references\upstream -> C:\Data\Hermes\~\autoresearch
#
# Why a junction inside the skill folder (not the whole skill folder):
#   - last30days / webwright / understand-anything junction the WHOLE skill
#     folder because their upstream IS a skills/ directory.
#   - autoresearch upstream is a research project, NOT a skill — it has no
#     SKILL.md. We own the SKILL.md and scripts locally, and just want the
#     upstream train.py / prepare.py / program.md / results.tsv reachable
#     from the skill via a stable references\upstream path.
#
# Idempotent: re-runs detect existing correct junction and exit [OK] without changes.
#
# Usage: powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\install_autoresearch_link.ps1

$ErrorActionPreference = 'Stop'

$LinkPath   = 'C:\Data\Hermes_0.17.0\skills\research\autoresearch\references\upstream'
$RepoPath   = 'C:\Data\Hermes\~\autoresearch'

# Verify source repo exists and has the markers the upstream README promises.
if (-not (Test-Path -LiteralPath $RepoPath)) {
    Write-Host "[FAIL] autoresearch repo not found: $RepoPath"
    exit 1
}
$expectedFiles = @('prepare.py', 'train.py', 'program.md', 'pyproject.toml', 'README.md')
foreach ($f in $expectedFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoPath $f))) {
        Write-Host "[FAIL] $RepoPath\$f missing"
        exit 1
    }
}

# Make sure the parent directory exists (skill folder must be present).
$parent = Split-Path -Path $LinkPath -Parent
if (-not (Test-Path -LiteralPath $parent)) {
    Write-Host "[FAIL] $parent missing -- install the autoresearch skill first"
    exit 1
}

# Already a junction/symlink? Verify it points to the right place.
if (Test-Path -LiteralPath $LinkPath) {
    $item = Get-Item -LiteralPath $LinkPath -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $target = $item.Target
        if ($target -eq $RepoPath) {
            Write-Host "[OK] junction already in place: $LinkPath -> $RepoPath"
            exit 0
        } else {
            Write-Host "[FAIL] $LinkPath exists but points to '$target' (expected '$RepoPath')"
            exit 1
        }
    } else {
        Write-Host ('[FAIL] ' + $LinkPath + ' exists but is NOT a junction/symlink. Remove it manually and re-run.')
        exit 1
    }
}

# Create the junction. mklink /J works in the user's own profile without admin.
# NOTE: invoke cmd directly with an array of args — passing a single quoted
# string through `cmd /c "..."` from MSYS bash mangles the target into a
# `C:\C:\...` double-prefixed path that resolves to nothing.
$cmdOut = & cmd.exe /c mklink /J "$LinkPath" "$RepoPath" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] mklink /J failed (exit $LASTEXITCODE): $cmdOut"
    exit 1
}

# Self-verify
$verify = Get-Item -LiteralPath $LinkPath -Force
if ($verify.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    # Touch a known upstream file through the junction to confirm resolution.
    $testFile = Join-Path $LinkPath 'program.md'
    if (-not (Test-Path -LiteralPath $testFile)) {
        Write-Host "[FAIL] junction created but $testFile is not reachable"
        exit 1
    }
    Write-Host "[OK] created junction: $LinkPath -> $RepoPath"
    Write-Host "[OK] upstream reachable through junction (verified program.md)"
    exit 0
} else {
    Write-Host "[FAIL] junction verification failed"
    exit 1
}