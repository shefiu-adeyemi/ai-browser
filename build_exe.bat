@echo off
rem Builds the launcher ("AI Browser Agent.exe") from launcher.py.
rem Needs:  pip install -r requirements-dev.txt
rem The result is NOT standalone: copy it into the project folder next to agent_ui.py.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean "AI Browser Agent.spec"
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)
echo.
echo Built: dist\AI Browser Agent.exe
echo Copy it to this folder (replacing the old one) - close the app first if it is running.
