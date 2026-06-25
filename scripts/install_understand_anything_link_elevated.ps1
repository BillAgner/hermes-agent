# install_understand_anything_link_elevated.ps1
# Runs as admin (re-launched by install_understand_anything_link.ps1). Creates the symlink.

$ErrorActionPreference = 'Stop'

$LinkPath   = Join-Path $env:USERPROFILE '.understand-anything-plugin'
$RepoPlugin = 'C:\Data\Hermes\~\Understand-Anything\understand-anything-plugin'

if (-not (Test-Path -LiteralPath $RepoPlugin)) {
    Write-Host "[FAIL] source plugin not found: $RepoPlugin"
    exit 1
}

if (Test-Path -LiteralPath $LinkPath) {
    Write-Host "[FAIL] $LinkPath already exists"
    exit 1
}

# cmd.exe mklink is the most reliable cross-shell invocation on Windows.
$result = cmd /c "mklink /D `"$LinkPath`" `"$RepoPlugin`""
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] mklink failed (exit $LASTEXITCODE): $result"
    exit 1
}

Write-Host "[OK] created symlink: $LinkPath -> $RepoPlugin"
exit 0
