$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDirectory = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$WheelDirectory = Join-Path $ProjectRoot "packages\wheels"
$Requirements = Join-Path $ProjectRoot "requirements-build.txt"

if (-not (Test-Path -LiteralPath $WheelDirectory)) {
    throw "未找到 packages\wheels。请先在网络好的电脑运行 准备离线依赖.ps1，再复制 packages 文件夹。"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv $VenvDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "无法创建项目内 Python 环境。请先安装 64 位 Python 3.12。"
    }
}

& $VenvPython -m pip install `
    --no-index `
    --find-links $WheelDirectory `
    --requirement $Requirements

if ($LASTEXITCODE -ne 0) {
    throw "离线依赖安装失败，退出代码：$LASTEXITCODE"
}

& $VenvPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "离线安装完成，但项目测试未通过。"
}

Write-Host "项目内离线环境已安装并通过测试：$VenvDirectory"
