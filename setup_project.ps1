$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip升级失败，退出代码：$LASTEXITCODE"
}
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) {
    throw "项目依赖安装失败，退出代码：$LASTEXITCODE"
}
Write-Host "项目内运行环境已安装：$ProjectRoot\.venv"
