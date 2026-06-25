@echo off
REM UAC trampoline -- right-click "Run as administrator" or invoke with -Verb RunAs.
REM Registers C:\Data\Hermes_0.17.0\scripts\startup_hermes.ps1 as the Hermes_Startup
REM AtStartup scheduled task. Run ONCE; rerun only to refresh the task config.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Data\Hermes_0.17.0\scripts\register-hermes-startup.ps1"
pause
