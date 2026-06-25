@echo off
REM last30days.cmd - Windows wrapper for last30days.py
REM Locates Python 3.12+ and invokes the engine with all args passed through.
REM
REM This wrapper exists because the engine's `#!/usr/bin/env python3` shebang
REM resolves to the Microsoft Store stub on Windows. Hermes-on-Windows hosts
REM install Python 3.12 separately and need an explicit interpreter.
REM
REM Search order:
REM   1. LAST30DAYS_PYTHON env var (explicit override)
REM   2. %LOCALAPPDATA%\Programs\Python\Python312\python.exe (Windows Store install)
REM   3. %LOCALAPPDATA%\Programs\Python\Python313\python.exe
REM   4. py -3.12 / py -3.13 (Windows Python launcher)

setlocal
set "PYEXE="

if not "%LAST30DAYS_PYTHON%"=="" (
    if exist "%LAST30DAYS_PYTHON%" (
        set "PYEXE=%LAST30DAYS_PYTHON%"
    )
)

if "%PYEXE%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if "%PYEXE%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)

if "%PYEXE%"=="" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.12 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.12"
        if "%PYEXE%"=="" (
            py -3.13 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.13"
        )
    )
)

if "%PYEXE%"=="" (
    echo [last30days] ERROR: Python 3.12+ not found. 1>&2
    echo   Tried LAST30DAYS_PYTHON, %%LOCALAPPDATA%%\Programs\Python\Python{312,313}\python.exe, and `py -3.12/-3.13`. 1>&2
    echo   Install Python 3.12+ or set LAST30DAYS_PYTHON=C:\path\to\python.exe. 1>&2
    exit /b 1
)

REM Resolve script dir relative to this wrapper
set "SCRIPT_DIR=%~dp0scripts"
if not exist "%SCRIPT_DIR%\last30days.py" (
    REM Fallback: scripts are co-located with this .cmd
    set "SCRIPT_DIR=%~dp0"
)

%PYEXE% "%SCRIPT_DIR%\last30days.py" %*
exit /b %ERRORLEVEL%
