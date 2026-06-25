# Enable People API for the Hermes Google Workspace project.
#
# Bill's google_token.json already grants contacts.readonly, but the People
# API is not yet enabled in the Google Cloud project (832564875509). Until
# it's enabled, the Contacts panel on the Google Dashboard shows a 403.
#
# This script opens the activation URL in the user's default browser so
# Bill can click ENABLE (one click, ~10 seconds), then it re-tests the
# /api/google/snapshot endpoint to verify contacts now return data.
#
# Idempotent: re-running just re-tests. Safe to run any time.

$ErrorActionPreference = "Stop"
$project = "832564875509"
$activationUrl = "https://console.developers.google.com/apis/api/people.googleapis.com/overview?project=$project"
$dashboard = "http://127.0.0.1:9119/api/google/snapshot"

Write-Host "[INFO] People API activation URL:" -ForegroundColor Cyan
Write-Host "       $activationUrl" -ForegroundColor Gray

# Open in default browser
try {
    Start-Process $activationUrl
    Write-Host "[OK]   Opened activation page in browser" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Could not open browser automatically: $_" -ForegroundColor Yellow
    Write-Host "       Please open the URL above manually" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[INFO] Click 'Enable' on the Google Cloud page, then press Enter here to verify..."
Read-Host "Press Enter after enabling"

Write-Host ""
Write-Host "[INFO] Testing /api/google/snapshot for contacts..." -ForegroundColor Cyan
try {
    $resp = Invoke-RestMethod -Uri $dashboard -TimeoutSec 15
    $contacts = $resp.contacts
    $status = $contacts.status
    $peopleN = ($contacts.people | Measure-Object).Count
    if ($status -eq "ok" -and $peopleN -gt 0) {
        Write-Host "[OK]   contacts.status=$status  people.count=$peopleN" -ForegroundColor Green
        Write-Host "       People API is live; Contacts panel will populate on next dashboard refresh." -ForegroundColor Green
        exit 0
    } elseif ($status -eq "ok") {
        Write-Host "[WARN] contacts.status=$status but people list is empty (no contacts in account yet)" -ForegroundColor Yellow
        exit 0
    } else {
        Write-Host "[FAIL] contacts.status=$status" -ForegroundColor Red
        Write-Host "       error: $($contacts.error)" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "[FAIL] Could not reach dashboard: $_" -ForegroundColor Red
    exit 1
}