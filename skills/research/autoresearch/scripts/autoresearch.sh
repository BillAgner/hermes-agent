#!/usr/bin/env bash
# autoresearch.sh - bash entry point for the autoresearch skill.
#
# Mirrors autoresearch.cmd. Use this from MSYS bash, WSL, or any POSIX shell.
#
# Usage:
#   ./autoresearch.sh status
#   ./autoresearch.sh analyze
#   ./autoresearch.sh setup
#   ./autoresearch.sh train
#   ./autoresearch.sh update
#
# NOTE on Windows: `command -v python3` returns the Microsoft App Execution
# Aliases stub (`Microsoft\WindowsApps\python3.exe`) which opens the Store
# instead of running Python. We resolve real Python via ABSOLUTE path first
# before falling back to PATH lookup.

set -e

REPO="${AUTORESEARCH_REPO:-C:/Data/Hermes/~/autoresearch}"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Python search order:
#   1. AUTORESEARCH_PYTHON env var (explicit override)
#   2. LAST30DAYS_PYTHON env var (shared with last30days skill)
#   3. Absolute paths to common Python 3.12/3.13 install dirs
#   4. PATH lookup, but ONLY if the resolved path is NOT the Microsoft Store stub
PYEXE="${AUTORESEARCH_PYTHON:-}"
if [ -z "$PYEXE" ] && [ -n "$LAST30DAYS_PYTHON" ] && [ -x "$LAST30DAYS_PYTHON" ]; then
    PYEXE="$LAST30DAYS_PYTHON"
fi
if [ -z "$PYEXE" ]; then
    for cand in \
        "/c/Users/bobup/AppData/Local/Programs/Python/Python312/python.exe" \
        "/c/Users/bobup/AppData/Local/Programs/Python/Python313/python.exe" \
        "/c/Python312/python.exe" \
        "/c/Python313/python.exe" \
        "/usr/bin/python3.12" "/usr/bin/python3.13" \
        "/usr/local/bin/python3.12" "/usr/local/bin/python3.13"
    do
        if [ -x "$cand" ]; then
            PYEXE="$cand"
            break
        fi
    done
fi
if [ -z "$PYEXE" ]; then
    for cand in python3.12 python3.13 python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            resolved="$(command -v "$cand" 2>/dev/null || true)"
            # Reject the Windows App Execution Aliases stub — it opens the Store.
            case "$resolved" in
                *WindowsApps/python3*|*WindowsApps/python.exe)
                    continue
                    ;;
            esac
            PYEXE="$resolved"
            break
        fi
    done
fi
if [ -z "$PYEXE" ]; then
    echo "[autoresearch] ERROR: real python not found in PATH" >&2
    echo "  Set AUTORESEARCH_PYTHON=/path/to/python or install Python 3.12+" >&2
    echo "  (Avoid Windows App Execution Aliases: \`python3\` opens the Store.)" >&2
    exit 1
fi

CMD="${1:-status}"
shift || true

case "$CMD" in
    status)
        "$PYEXE" "$SKILL_DIR/scripts/status.py" "$@"
        ;;
    analyze)
        "$PYEXE" "$SKILL_DIR/scripts/analyze.py" "$@"
        ;;
    setup)
        if [ ! -d "$REPO" ]; then
            echo "[autoresearch] FAIL: repo not found at $REPO" >&2
            exit 1
        fi
        cd "$REPO"
        echo "[autoresearch] uv sync ..."
        uv sync
        echo "[autoresearch] prepare.py - downloads data + trains tokenizer ..."
        uv run prepare.py
        echo "[autoresearch] OK setup complete"
        ;;
    train)
        if [ ! -d "$REPO" ]; then
            echo "[autoresearch] FAIL: repo not found at $REPO" >&2
            exit 1
        fi
        cd "$REPO"
        uv run train.py "$@"
        ;;
    update)
        if [ ! -d "$REPO" ]; then
            echo "[autoresearch] FAIL: repo not found at $REPO" >&2
            exit 1
        fi
        cd "$REPO"
        git pull --ff-only
        ;;
    *)
        echo "[autoresearch] unknown command: $CMD" >&2
        echo "  valid: status, analyze, setup, train, update" >&2
        exit 1
        ;;
esac