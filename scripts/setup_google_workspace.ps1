# setup_google_workspace.ps1 — single-file Gmail/Calendar OAuth setup for Hermes.
#
# Walks you through Google's OAuth flow once. Stores client_secret at
# C:\Data\Hermes_0.17.0\google_client_secret.json and the resulting token at
# %USERPROFILE%\.hermes\google_token.json.
#
# After this completes, the next phase is to build/wire the gmail/calendar
# MCP server — that's deferred until the OAuth flow succeeds at least once.
#
# Single-command usage (no UAC, no admin needed):
#   powershell -ExecutionPolicy Bypass -File C:\Data\Hermes_0.17.0\scripts\setup_google_workspace.ps1
#
# Prerequisites (you provide once):
#   - A Google Cloud project with the Gmail API and Calendar API enabled.
#   - OAuth 2.0 Client ID of type "Desktop app", downloaded as client_secret.json.
#   - Place it at: C:\Data\Hermes_0.17.0\google_client_secret.json

$ClientSecretPath = "C:\Data\Hermes_0.17.0\google_client_secret.json"
$SkillDir         = "C:\Data\Hermes_0.17.0\skills\productivity\google-workspace"
$TokenPath        = (Join-Path $env:USERPROFILE ".hermes\google_token.json")
$PythonExe        = "C:\Data\Hermes_0.17.0 0.17.0\venv\Scripts\python.exe"

function Step($m) { Write-Host "[setup] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

# 1. Skill present?
Step "Verifying google-workspace skill at $SkillDir"
if (-not (Test-Path $SkillDir)) { Fail "google-workspace skill not found at $SkillDir" }
$SetupScript = Join-Path $SkillDir "scripts\setup.py"
if (-not (Test-Path $SetupScript)) { Fail "setup.py not found at $SetupScript" }
Ok "skill present"

# 2. Python in the Hermes venv?
Step "Verifying Python at $PythonExe"
if (-not (Test-Path $PythonExe)) { Fail "Python not found at $PythonExe" }
$pyVer = (& $PythonExe --version 2>&1)
Ok "python present ($pyVer)"

# --- 3. OAuth client config from .env OR legacy client_secret.json -----
# (Previously required a client_secret.json file at $ClientSecretPath. As of
# 2026-06-20, setup.py reads GOOGLE_OAUTH_CLIENT_ID / _CLIENT_SECRET from
# .env directly, so the file is no longer required. We just smoke-test that
# setup.py can build a client config from whichever source is available.)
Step "Verifying Google OAuth client config (.env preferred, client_secret.json legacy)"
$TestClientConfig = "import sys; sys.path.insert(0, r'" + $SkillDir + "\scripts'); " +
    "from setup import _oauth_client_config; " +
    "cfg = _oauth_client_config(); " +
    "cid = cfg.get('installed', cfg.get('web', {})).get('client_id', ''); " +
    "print('client_id=' + cid)"
$clientCheck = (& $PythonExe -c $TestClientConfig 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Fail ("setup.py could not build OAuth client config: " + $clientCheck)
}
if ($clientCheck -notmatch "^client_id=.+") {
    Fail ("setup.py did not return a client_id from env or file: " + $clientCheck)
}
$redactedCid = ($clientCheck -replace "client_id=(\S{12}).+", 'client_id=$1...')
Ok ("OAuth client config OK (" + $redactedCid + ")")

# 4. (Removed) — previously stored client_secret.json via setup.py --client-secret.
# No longer needed: setup.py reads GOOGLE_OAUTH_CLIENT_ID/_CLIENT_SECRET from .env
# (managed by the dashboard KEYS page). The legacy --client-secret flag still
# works for users who prefer the JSON-file flow.

# 5. Check current auth state
Step "Checking existing OAuth token"
& $PythonExe $SetupScript --check | Out-Null
$NeedAuth = ($LASTEXITCODE -ne 0)

if ($NeedAuth)
{
    Warn "no valid token - starting OAuth dance"

    # 6a. Print the auth URL
    Step "Generating OAuth URL..."
    $authUrl = (& $PythonExe $SetupScript --auth-url 2>&1 | Out-String).Trim()
    Write-Host ""
    Write-Host "  Open this URL in your browser, authorize, and copy the code:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  $authUrl" -ForegroundColor White
    Write-Host ""

    # 6b. Prompt for the code
    $code = Read-Host "  Paste the authorization code here"
    if ([string]::IsNullOrWhiteSpace($code)) { Fail "no code provided - aborting" }

    # 6c. Exchange the code for a token
    Step "Exchanging code for token..."
    & $PythonExe $SetupScript --auth-code $code | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "setup.py --auth-code failed (exit $LASTEXITCODE)" }
    Ok "token stored at $TokenPath"
}

if (-not $NeedAuth)
{
    Ok "token already valid - no OAuth dance needed"
}

# 7. Verify the token works
Step "Verifying token against Gmail + Calendar APIs"
& $PythonExe $SetupScript --check | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "token check failed - re-run this script to re-authorize" }
Ok "token verified"

# 8. Smoke-test the APIs directly via the skill's google_api.py
$googleApi = Join-Path $SkillDir "scripts\google_api.py"
if (Test-Path $googleApi)
{
    Step "Smoke-testing Gmail..."
    $gmailOut = (& $PythonExe $googleApi gmail list --max-results 1 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0) { Ok "Gmail reachable" }
    else { Warn "Gmail smoke-test failed (token may lack gmail.readonly scope): $gmailOut" }

    Step "Smoke-testing Calendar..."
    $calOut = (& $PythonExe $googleApi calendar list --max-results 1 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0) { Ok "Calendar reachable" }
    else { Warn "Calendar smoke-test failed: $calOut" }
}

Write-Host ""
Ok "Google Workspace setup complete."
Write-Host ""
Write-Host "Next steps (deferred until you ask):" -ForegroundColor Cyan
Write-Host "  1. Build a google-workspace-mcp server wrapping Gmail/Calendar." -ForegroundColor White
Write-Host "  2. Register it via: hermes mcp add google_workspace --command ..." -ForegroundColor White
Write-Host "  3. Wire dashboard panels at 127.0.0.1:9119 for inbox + calendar." -ForegroundColor White
