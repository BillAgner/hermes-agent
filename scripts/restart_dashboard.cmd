@echo off
rem Restart the Hermes dashboard process so source edits to web_server.py take effect.
rem Usage:  scripts\restart_dashboard.cmd
rem Bill asked to avoid gateway restarts; this restarts ONLY the dashboard process (port 9119),
rem not the messaging gateway (Telegram/discord). The watchdog will see the dashboard
rem exit and bring it back automatically.
setlocal
cd /d C:\Data\Hermes_0.17.0
echo [%date% %time%] Restarting dashboard...
taskkill /F /PID 49848 2>nul || echo dashboard PID 49848 not found
echo waiting for watchdog to bring it back (5s)...
timeout /t 5 /nobreak >nul
netstat -ano | findstr ":9119.*LISTENING" | head -1
endlocal