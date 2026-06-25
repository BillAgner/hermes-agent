@echo off
REM Hermes 0.17.0 launcher -- starts the parallel v0.17.0 install with a clean
REM runtime environment. Scrubs PYTHONPATH/VIRTUAL_ENV/HERMES_HOME inherited
REM from the parent shell so the trial reads its own config + venv.

set PYTHONPATH=
set VIRTUAL_ENV=C:\Data\Hermes_0.17.0\venv
set HERMES_HOME=C:\Data\Hermes_0.17.0
set "PATH=C:\Data\Hermes_0.17.0\venv\Scripts;%PATH%"
cd /d "C:\Data\Hermes_0.17.0"
"C:\Data\Hermes_0.17.0\venv\Scripts\hermes.exe" %*
