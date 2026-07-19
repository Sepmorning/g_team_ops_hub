$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$WheelDirectory = Join-Path $ProjectRoot "packages\wheels"
$Requirements = Join-Path $ProjectRoot "requirements-build.txt"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "请先运行 setup_project.ps1 建立项目内环境。"
}

New-Item -ItemType Directory -Path $WheelDirectory -Force | Out-Null

& $VenvPython -m pip download `
    --only-binary=:all: `
    --destination-directory $WheelDirectory `
    --requirement $Requirements

if ($LASTEXITCODE -ne 0) {
    throw "离线依赖下载失败，退出代码：$LASTEXITCODE"
}

Write-Host "离线依赖已保存到：$WheelDirectory"
Write-Host "可将整个 packages 文件夹复制到另一台 Windows 电脑的项目根目录。"
