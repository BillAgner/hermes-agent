@echo off
rem Hermes Agent Gateway - Messaging Platform Integration + Dashboard
cd /d "C:\Data\Hermes_0.17.0"
set "HERMES_HOME=C:\Data\Hermes_0.17.0"
set "PYTHONIOENCODING=utf-8"
set "HERMES_GATEWAY_DETACHED=1"
set "VIRTUAL_ENV=C:\Data\Hermes_0.17.0\venv"
rem Start dashboard web UI (port 9119) as a detached background process
start "" /B cmd /c "C:\Data\Hermes_0.17.0\gateway-service\Hermes_Dashboard.cmd"
rem Start messaging gateway + cron scheduler (foreground, task scheduler tracks this)
rem Use pythonw.exe (no console window) to prevent accidental closure by user.
rem Crash logs (Python exception traces) are written by gateway_launcher.py.
"C:\Data\Hermes_0.17.0\venv\Scripts\pythonw.exe" "C:\Data\Hermes_0.17.0\scripts\gateway_launcher.py"
exit /b 0
