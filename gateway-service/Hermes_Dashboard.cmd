@echo off
rem Hermes Agent Dashboard - Web UI server on port 9119
cd /d "C:\Data\Hermes_0.17.0"
set "HERMES_HOME=C:\Data\Hermes_0.17.0"
set "PYTHONIOENCODING=utf-8"
set "VIRTUAL_ENV=C:\Data\Hermes_0.17.0\venv"
"C:\Data\Hermes_0.17.0\venv\Scripts\pythonw.exe" "C:\Data\Hermes_0.17.0\scripts\dashboard_launcher.py"
exit /b 0
