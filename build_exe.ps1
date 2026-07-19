$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path $ProjectRoot ".build-exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "请先运行 setup_project.ps1 安装项目内环境。"
}

if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TempRoot | Out-Null

Push-Location $ProjectRoot
try {
    & $VenvPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "AndaFbaTracker" `
        --distpath (Join-Path $TempRoot "dist") `
        --workpath (Join-Path $TempRoot "work") `
        --specpath $TempRoot `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "打包失败，退出代码：$LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $TempRoot "dist\AndaFbaTracker.exe") `
        -Destination (Join-Path $ProjectRoot "AndaFbaTracker.exe") -Force
}
finally {
    Pop-Location
}

Remove-Item -LiteralPath $TempRoot -Recurse -Force
Write-Host "EXE 已生成：$ProjectRoot\AndaFbaTracker.exe"

