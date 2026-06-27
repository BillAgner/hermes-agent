#!/usr/bin/env bash
# verify_cron_job.sh — One-shot verifier for a newly-created hermes cron job.
#
# Usage:  bash verify_cron_job.sh <job-name-or-id>
#
# Checks (in order, stops at first failure):
#   1. hermes cron list        → job appears, [active], expected fields present
#   2. hermes cron status      → gateway is running
#   3. script-path reachability → the script the scheduler points at exists
#                                  in BOTH ~/.hermes/scripts/ AND the source
#                                  install location (the script-location
#                                  pitfall that silently breaks runs)
#   4. triggered test run      → `hermes cron run` followed by ~75s wait, then
#                                  `hermes cron show` confirms ok status
#
# Exits 0 on full pass, 1 on any failure. Echoes a clear ✅ / ❌ line per step.

set -u
HERMES="/c/Data/Hermes 0.17.0/venv/Scripts/hermes.exe"
SOURCE_SCRIPTS="/c/Data/Hermes 0.17.0/scripts"
USER_SCRIPTS="$HOME/.hermes/scripts"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <job-name-or-id>" >&2
  exit 2
fi
JOB="$1"
echo "=== verify_cron_job.sh: $JOB ==="

# --- Step 1: gateway status ---------------------------------------------------
echo
echo "--- [1/4] gateway status ---"
if "$HERMES" cron status >/dev/null 2>&1; then
  echo "✅ gateway running"
else
  echo "❌ gateway NOT running — start it before scheduling jobs"
  exit 1
fi

# --- Step 2: job registered & active -----------------------------------------
echo
echo "--- [2/4] job registered & active ---"
LIST_OUT=$("$HERMES" cron list 2>&1)
if echo "$LIST_OUT" | grep -q "$JOB"; then
  echo "✅ job found in list"
else
  echo "❌ job '$JOB' not in cron list"
  echo "    hint: hermes cron list  →  check spelling"
  exit 1
fi
if echo "$LIST_OUT" | grep -B1 -A2 "$JOB" | grep -q "active"; then
  echo "✅ job is active"
else
  echo "❌ job is not in 'active' state — pause/remove and re-create"
  exit 1
fi

# --- Step 3: script reachability --------------------------------------------
echo
echo "--- [3/4] script reachability ---"
SCRIPT_NAME=$("$HERMES" cron show "$JOB" 2>&1 | awk -F': *' '/Script:/ {print $2; exit}')
if [[ -z "${SCRIPT_NAME:-}" ]]; then
  echo "ℹ️  job has no --script (prompt-based) — skipping script checks"
else
  if [[ -f "$SOURCE_SCRIPTS/$SCRIPT_NAME" ]]; then
    echo "✅ source install has it: $SOURCE_SCRIPTS/$SCRIPT_NAME"
  else
    echo "❌ script not found at $SOURCE_SCRIPTS/$SCRIPT_NAME (this is what the scheduler reads)"
    echo "    fix: cp $USER_SCRIPTS/$SCRIPT_NAME $SOURCE_SCRIPTS/$SCRIPT_NAME"
    exit 1
  fi
  if [[ -f "$USER_SCRIPTS/$SCRIPT_NAME" ]]; then
    echo "✅ user-symlinked copy present: $USER_SCRIPTS/$SCRIPT_NAME"
  else
    echo "⚠️  no copy in $USER_SCRIPTS — only matters if you edit the source one and want the symlink to track"
  fi
fi

# --- Step 4: test run ---------------------------------------------------------
echo
echo "--- [4/4] trigger test run, wait ~75s for tick + completion ---"
"$HERMES" cron run "$JOB" 2>&1 | sed 's/^/    /'
echo "    (waiting for gateway tick... ~75s)"
sleep 75
SHOW_OUT=$("$HERMES" cron show "$JOB" 2>&1)
LAST_LINE=$(echo "$SHOW_OUT" | grep "Last run:" | tail -1)
echo "    $LAST_LINE"
if echo "$LAST_LINE" | grep -q "ok"; then
  echo "✅ test run succeeded"
  echo
  echo "=== $JOB verified ✅ — next scheduled run will fire automatically ==="
  exit 0
else
  echo "❌ test run did not finish with 'ok'"
  echo "    full show output:"
  echo "$SHOW_OUT" | sed 's/^/    /'
  exit 1
fi
