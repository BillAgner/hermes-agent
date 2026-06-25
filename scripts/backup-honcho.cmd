@echo off
REM Honcho backup wrapper. Runs the bash backup script inside WSL
REM hermes-tools distro (Ubuntu 24.04) which has docker compose + pg_dump.
REM Output is delivered verbatim by the no-agent cron job.

setlocal

set "WSL_DISTRO=hermes-tools"
set "SCRIPT=/mnt/c/Data/Hermes/scripts/backup-honcho.sh"
set "LOG_DIR=C:\Data\Hermes_0.17.0\cron\output\backup-honcho"
set "TZ_OFFSET_HOURS=-7"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

wsl.exe -d %WSL_DISTRO% -- bash -lc "TZ_OFFSET_HOURS=%TZ_OFFSET_HOURS% bash %SCRIPT%" 2>&1
exit /b %ERRORLEVEL%
