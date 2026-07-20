@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 项目环境不存在，请先运行 setup_project.ps1 或 离线安装项目.ps1
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
