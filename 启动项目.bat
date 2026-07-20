@echo off
cd /d "%~dp0"
if exist "AndaFbaTracker.exe" (
    start "" "%~dp0AndaFbaTracker.exe"
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b 0
)
echo Missing AndaFbaTracker.exe and project environment.
echo Run setup_project.ps1 first.
pause
exit /b 1
