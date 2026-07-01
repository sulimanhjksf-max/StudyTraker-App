@echo off
title Study Tracker
cd /d "%~dp0"
echo Starting Study Tracker...
start /b python app.py
echo Waiting for server...
:wait
ping -n 2 127.0.0.1 > nul
powershell -command "try { Invoke-WebRequest http://localhost:5001 -TimeoutSec 1 -UseBasicParsing | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 goto wait
start "" "http://localhost:5001"
echo.
echo StudyTracker is running at http://localhost:5001
echo Close this window to stop the server.
echo.
pause
taskkill /f /fi "WINDOWTITLE eq Study Tracker" /im python.exe > nul 2>&1
