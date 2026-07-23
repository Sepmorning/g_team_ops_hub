$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "项目环境不存在，请先运行 setup_project.ps1"
}

Set-Location $ProjectRoot
& $Python web_main.py

