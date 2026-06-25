@echo off
REM Hermes 0.17.0 Python launcher -- invokes the trial's Python with a clean env.

set PYTHONPATH=
set VIRTUAL_ENV=C:\Data\Hermes_0.17.0\venv
set HERMES_HOME=C:\Data\Hermes_0.17.0
cd /d "C:\Data\Hermes_0.17.0"
"C:\Data\Hermes_0.17.0\venv\Scripts\python.exe" %*
