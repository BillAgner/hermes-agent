@echo off
REM autoresearch.cmd - Windows entry point for the autoresearch skill.
REM
REM Usage:
REM   autoresearch.cmd status                 preflight check
REM   autoresearch.cmd analyze                summarize results.tsv
REM   autoresearch.cmd plot                   render progress.png from results.tsv
REM   autoresearch.cmd delegate               print the remote-CUDA launch command
REM   autoresearch.cmd setup                  uv sync + prepare.py (CPU only)
REM   autoresearch.cmd train                  uv run train.py (needs CUDA)
REM   autoresearch.cmd update                 git pull in autoresearch checkout
REM
REM Python search order:
REM   1. AUTORESEARCH_PYTHON env var
REM   2. LAST30DAYS_PYTHON (same Python 3.12 the last30days skill uses)
REM   3. %LOCALAPPDATA%\Programs\Python\Python312\python.exe
REM   4. %LOCALAPPDATA%\Programs\Python\Python313\python.exe
REM   5. py -3.12 / py -3.13 (Windows Python launcher)
REM
REM CMD 5.1 GOTCHA: inside a parenthesized if-block, %ERRORLEVEL% and %VAR%
REM are expanded at PARSE time, before any command inside runs. Without
REM EnableDelayedExpansion, `set "RC=%ERRORLEVEL%"` captures the OLD error
REM level and `exit /b %RC%` exits with the wrong code. The fix is
REM setlocal EnableDelayedExpansion + !ERRORLEVEL! (note the bangs).
REM
REM Empirically verified: without delayed expansion, analyze.py's sys.exit(1)
REM comes through as exit /b 0 because %ERRORLEVEL% parses to "" before
REM python runs. With !ERRORLEVEL!, the real exit code propagates.

setlocal EnableDelayedExpansion

set "REPO=C:\Data\Hermes\~\autoresearch"
set "SKILL_DIR=C:\Data\Hermes_0.17.0\skills\research\autoresearch"

set "PYEXE="

if not "%AUTORESEARCH_PYTHON%"=="" (
    if exist "%AUTORESEARCH_PYTHON%" set "PYEXE=%AUTORESEARCH_PYTHON%"
)

if "!PYEXE!"=="" (
    if not "%LAST30DAYS_PYTHON%"=="" (
        if exist "%LAST30DAYS_PYTHON%" set "PYEXE=%LAST30DAYS_PYTHON%"
    )
)

if "!PYEXE!"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if "!PYEXE!"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)

if "!PYEXE!"=="" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.12 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.12"
        if "!PYEXE!"=="" (
            py -3.13 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.13"
        )
    )
)

if "!PYEXE!"=="" (
    echo [autoresearch] ERROR: Python 3.12+ not found. 1>&2
    echo   Set AUTORESEARCH_PYTHON=C:\path\to\python.exe or install Python 3.12+. 1>&2
    exit /b 1
)

set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

if /I "%CMD%"=="status" (
    "!PYEXE!" "%SKILL_DIR%\scripts\status.py" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b !ERRORLEVEL!
)

if /I "%CMD%"=="analyze" (
    "!PYEXE!" "%SKILL_DIR%\scripts\analyze.py" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b !ERRORLEVEL!
)

if /I "%CMD%"=="plot" (
    REM plot.py needs matplotlib — use the autoresearch .venv if present,
    REM else fall through to the system Python (will error with a clear
    REM "matplotlib not installed" message if neither has it).
    REM Use a single Python invocation then ONE exit at the outer scope —
    REM nested `exit /b` inside parenthesized `if` blocks in CMD silently
    REM drops the inner exit code (the outer block continues and exits 0).
    if exist "%REPO%\.venv\Scripts\python.exe" (
        "%REPO%\.venv\Scripts\python.exe" "%SKILL_DIR%\scripts\plot.py" %2 %3 %4 %5 %6 %7 %8 %9
    ) else (
        "!PYEXE!" "%SKILL_DIR%\scripts\plot.py" %2 %3 %4 %5 %6 %7 %8 %9
    )
    exit /b !ERRORLEVEL!
)

if /I "%CMD%"=="delegate" (
    "!PYEXE!" "%SKILL_DIR%\scripts\delegate.py" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b !ERRORLEVEL!
)

if /I "%CMD%"=="setup" (
    if not exist "%REPO%" (
        echo [autoresearch] FAIL: repo not found at %REPO% 1>&2
        exit /b 1
    )
    pushd "%REPO%"
    echo [autoresearch] uv sync ...
    uv sync
    if errorlevel 1 (
        echo [autoresearch] FAIL: uv sync 1>&2
        popd
        exit /b 1
    )
    echo [autoresearch] prepare.py - downloads data + trains tokenizer ...
    uv run prepare.py
    set "RC=!ERRORLEVEL!"
    popd
    if not "!RC!"=="0" (
        echo [autoresearch] FAIL: prepare.py exited !RC! 1>&2
        exit /b !RC!
    )
    echo [autoresearch] OK setup complete
    exit /b 0
)

if /I "%CMD%"=="train" (
    if not exist "%REPO%" (
        echo [autoresearch] FAIL: repo not found at %REPO% 1>&2
        exit /b 1
    )
    pushd "%REPO%"
    uv run train.py %2 %3 %4 %5 %6 %7 %8 %9
    set "RC=!ERRORLEVEL!"
    popd
    exit /b !RC!
)

if /I "%CMD%"=="update" (
    if not exist "%REPO%" (
        echo [autoresearch] FAIL: repo not found at %REPO% 1>&2
        exit /b 1
    )
    pushd "%REPO%"
    git pull --ff-only
    set "RC=!ERRORLEVEL!"
    popd
    exit /b !RC!
)

echo [autoresearch] unknown command: %CMD% 1>&2
echo   valid: status, analyze, plot, delegate, setup, train, update 1>&2
exit /b 1