# setup_google_env_keys.ps1 — register Google OAuth env vars in .env so the
# dashboard KEYS page manages them, and add them to _EXTRA_ENV_KEYS in
# hermes_cli/config.py so the KEYS page knows about them.
#
# Single-command usage (no UAC needed):
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\setup_google_env_keys.ps1
#
# What this adds to .env (idempotent — skips entries that already exist):
#   GOOGLE_OAUTH_CLIENT_ID        (fill via KEYS page)
#   GOOGLE_OAUTH_CLIENT_SECRET    (fill via KEYS page)
#   GOOGLE_OAUTH_TOKEN_PATH       (defaults to %HERMES_HOME%\google_token.json)
#   GOOGLE_OAUTH_SCOPES           (defaults to Gmail + Calendar readonly+send)
#
# What this patches in config.py:
#   Adds the same four names to _EXTRA_ENV_KEYS so the KEYS page surfaces them.
#
# After this script runs:
#   - Open http://127.0.0.1:9119/keys
#   - You'll see four new entries (Google OAuth section)
#   - Paste your Client ID and Client Secret into the first two
#   - Leave the token path at the default; it gets populated by setup_google_workspace.ps1
#   - Restart the dashboard / gateway so the keys reload

$EnvPath        = "C:\Data\Hermes_0.17.0\.env"
$ConfigPath     = "C:\Data\Hermes_0.17.0\hermes-agent\hermes_cli\config.py"
$HermesHome     = "C:\Data\Hermes_0.17.0"
$DefaultToken   = "$HermesHome\google_token.json"

# The four env keys to ensure
$EnvKeys = @(
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_TOKEN_PATH",
    "GOOGLE_OAUTH_SCOPES"
)

function Step($m) { Write-Host "[setup] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

# --- 1. .env file --------------------------------------------------------
Step "Checking .env at $EnvPath"
if (-not (Test-Path $EnvPath)) { Fail ".env not found at $EnvPath" }
Ok ".env present"

$envContent = Get-Content $EnvPath -Raw

$inserted = 0
$skipped  = 0
foreach ($k in $EnvKeys) {
    if ($envContent -match "(?m)^$([regex]::Escape($k))\s*=") {
        $skipped++
        continue
    }
    # Append at end (Hermes convention: a clearly labelled block at the bottom)
    switch ($k) {
        "GOOGLE_OAUTH_CLIENT_ID"     { $comment = "# Google OAuth: client identifier from console.cloud.google.com/apis/credentials (Public)" }
        "GOOGLE_OAUTH_CLIENT_SECRET" { $comment = "# Google OAuth: client secret paired with the ID above (Private - do not commit)" }
        "GOOGLE_OAUTH_TOKEN_PATH"    { $comment = "# Google OAuth: where setup_google_workspace.ps1 stores the resulting token JSON (auto-managed)" }
        "GOOGLE_OAUTH_SCOPES"        { $comment = "# Google OAuth: comma-separated scopes. Default covers Gmail + Calendar." }
        default                       { $comment = "# Google OAuth" }
    }
    $defaultValue = ""
    if ($k -eq "GOOGLE_OAUTH_TOKEN_PATH") { $defaultValue = $DefaultToken }
    if ($k -eq "GOOGLE_OAUTH_SCOPES") {
        $defaultValue = "https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.send,https://www.googleapis.com/auth/calendar"
    }
    $line = "$comment`n$k=$defaultValue"
    Add-Content -Path $EnvPath -Value "`n$line"
    $inserted++
    Ok "added $k to .env"
}
if ($inserted -eq 0) {
    Ok "all $skipped Google OAuth env keys already present in .env (no duplicates)"
}

# --- 2. config.py _EXTRA_ENV_KEYS patch --------------------------------
Step "Checking _EXTRA_ENV_KEYS in $ConfigPath"
if (-not (Test-Path $ConfigPath)) { Fail "config.py not found at $ConfigPath" }
$cfgContent = Get-Content $ConfigPath -Raw

# Idempotent: check if all keys are already in _EXTRA_ENV_KEYS
$missing = @()
foreach ($k in $EnvKeys) {
    if ($cfgContent -notmatch [regex]::Escape("`"$k`"")) {
        $missing += $k
    }
}

if ($missing.Count -eq 0)
{
    Ok "all four keys already present in _EXTRA_ENV_KEYS (no patch needed)"
}
else
{
    Step ("Adding " + $missing.Count + " new keys to _EXTRA_ENV_KEYS: " + ($missing -join ', '))
    # Insert each missing key on its own line just before the closing `})` of _EXTRA_ENV_KEYS.
    # We anchor on the last entry (`"LANGFUSE_BASE_URL",`) followed by `})`.
    $newLines = ($missing | ForEach-Object { '    "' + $_ + '",' }) -join "`n"
    # Find the closing pattern via plain string ops — PowerShell `-replace`
    # interprets `\r`/`\n` literally inside single-quoted regex strings, so
    # the safer path here is index-based insertion.
    $anchor = '    "LANGFUSE_BASE_URL",'
    # closeToken is `}` + `)` + CR + LF (the brace closes the set; the paren
    # closes frozenset(); CRLF ends the line). The file is CRLF throughout.
    $closeToken = "})`r`n"
    $anchorIdx = $cfgContent.IndexOf($anchor)
    if ($anchorIdx -lt 0) {
        Fail "could not find anchor '$anchor' in config.py"
    }
    $closeIdx = $cfgContent.IndexOf($closeToken, $anchorIdx)
    if ($closeIdx -lt 0) {
        Fail "could not find frozenset close '})' + CRLF after LANGFUSE_BASE_URL"
    }
    # Build the insertion: anchor + CRLF + new lines (joined with CRLF) + CRLF + closing sequence
    $newLinesCrlf = ($missing | ForEach-Object { '    "' + $_ + '",' }) -join "`r`n"
    $insertion = $anchor + "`r`n" + $newLinesCrlf + "`r`n" + $closeToken
    $patched = $cfgContent.Substring(0, $anchorIdx) + $insertion + $cfgContent.Substring($closeIdx + $closeToken.Length)
    if ($patched -eq $cfgContent) {
        Fail "could not patch config.py - no change produced"
    }
    Set-Content -Path $ConfigPath -Value $patched -Encoding UTF8 -NoNewline
    # Verify the patch took
    $verify = Get-Content $ConfigPath -Raw
    $stillMissing = @()
    foreach ($k in $missing) {
        if ($verify -notmatch [regex]::Escape('"' + $k + '"')) {
            $stillMissing += $k
        }
    }
    if ($stillMissing.Count -gt 0) {
        Fail ("patch did not take. Still missing: " + ($stillMissing -join ', '))
    }
    Ok ("patched config.py: added " + $missing.Count + " keys to _EXTRA_ENV_KEYS")
}

# --- 3. Verify Python can parse config.py ------------------------------
Step "Syntax-check config.py"
$py = "C:\Data\Hermes_0.17.0 0.17.0\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Fail "Python not found at $py" }
$pyCheck = "import hermes_cli.config as c; print('ok')"
$check = & $py -c $pyCheck 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail ("config.py failed to import: " + $check)
}
Ok "config.py parses and imports cleanly"

# --- 4. Final report ----------------------------------------------------
Write-Host ""
Ok "Google OAuth env keys are wired into .env and config.py."
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Restart the dashboard so the KEYS page picks up the new entries:" -ForegroundColor White
Write-Host "       hermes dashboard restart" -ForegroundColor White
Write-Host "  2. Open http://127.0.0.1:9119/keys" -ForegroundColor White
Write-Host "  3. Paste your Google OAuth Client ID and Client Secret into the new entries" -ForegroundColor White
Write-Host "  4. Run setup_google_workspace.ps1 to do the OAuth dance and populate the token file" -ForegroundColor White
Write-Host ""
