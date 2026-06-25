# install_understand_anything_link.ps1
# Creates C:\Users\bobup\.understand-anything-plugin -> C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin
# Required by the Understand-Anything plugin's skill at Phase 0.5 to locate its plugin root.
# Uses a directory junction (mklink /J) which does NOT require admin in the user's own profile.
# Idempotent: re-runs detect existing correct link and exit [OK] without changes.
#
# Usage: powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\install_understand_anything_link.ps1

$ErrorActionPreference = 'Stop'

$LinkPath   = Join-Path $env:USERPROFILE '.understand-anything-plugin'
$RepoPlugin = 'C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin'

# Verify the source plugin exists and has the markers UA's skill expects.
if (-not (Test-Path -LiteralPath $RepoPlugin)) {
    Write-Host "[FAIL] source plugin not found: $RepoPlugin"
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoPlugin 'package.json'))) {
    Write-Host "[FAIL] $RepoPlugin\package.json missing"
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoPlugin 'pnpm-workspace.yaml'))) {
    Write-Host "[FAIL] $RepoPlugin\pnpm-workspace.yaml missing"
    exit 1
}

# Already a junction/symlink? Verify it points to the right place.
if (Test-Path -LiteralPath $LinkPath) {
    $item = Get-Item -LiteralPath $LinkPath -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $target = $item.Target
        if ($target -eq $RepoPlugin) {
            Write-Host "[OK] junction already in place: $LinkPath -> $RepoPlugin"
            exit 0
        } else {
            Write-Host "[FAIL] $LinkPath exists but points to '$target' (expected '$RepoPlugin')"
            exit 1
        }
    } else {
        Write-Host "[FAIL] $LinkPath exists but is NOT a junction/symlink. Remove it manually and re-run."
        exit 1
    }
}

# Create the junction. mklink /J works in the user's own profile without admin.
# NOTE: invoke cmd directly with an array of args — passing a single quoted
# string through `cmd /c "..."` from MSYS bash mangles the target into a
# `C:\C:\...` double-prefixed path that resolves to nothing.
$cmdOut = & cmd.exe /c mklink /J "$LinkPath" "$RepoPlugin" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] mklink /J failed (exit $LASTEXITCODE): $cmdOut"
    exit 1
}

# Self-verify
$verify = Get-Item -LiteralPath $LinkPath -Force
if ($verify.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Write-Host "[OK] created junction: $LinkPath -> $RepoPlugin"
    exit 0
} else {
    Write-Host "[FAIL] junction verification failed"
    exit 1
}
