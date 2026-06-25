@echo off
REM Wrapper that re-launches fix-wsl-keepalive.ps1 with elevation if needed.
REM Single-line entry point for the user.

setlocal
set "SCRIPT=%~dp0fix-wsl-keepalive.ps1"

REM Already admin? Run directly.
net session >nul 2>&1
if %ERRORLEVEL%==0 goto :run

REM Otherwise self-elevate via UAC.
powershell.exe -NoProfile -Command "Start-Process -FilePath '%SCRIPT%' -Verb RunAs -Wait"
exit /b %ERRORLEVEL%

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
exit /b %ERRORLEVEL%