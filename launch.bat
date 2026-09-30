@echo off
title AI Browser Agent
cd /d "%~dp0"

:: Kill any previous instance holding port 8000
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /F /T /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting AI Browser Agent...
echo Open http://localhost:8000 in your browser
echo.
start /b "" python agent_ui.py
timeout /t 4 /nobreak >nul
start "" "http://localhost:8000"
