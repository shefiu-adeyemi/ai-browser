@echo off
rem Thin wrapper: all the logic (finding Python, stopping our old copy, waiting for the server,
rem opening the browser) lives in launcher.py so every way of starting the app behaves the same.
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (python launcher.py) else (py -3 launcher.py)
