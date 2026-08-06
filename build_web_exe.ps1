$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path $ProjectRoot ".build-web-exe"
$OutputExe = Join-Path $ProjectRoot "FbaTrackerWeb.exe"
$Templates = Join-Path $ProjectRoot "anda_tracker\web\templates"
$Static = Join-Path $ProjectRoot "anda_tracker\web\static"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Run setup_project.ps1 to install the project environment first."
}
if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TempRoot | Out-Null

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name "FbaTrackerWeb" `
        --add-data "$Templates;anda_tracker\web\templates" `
        --add-data "$Static;anda_tracker\web\static" `
        --distpath (Join-Path $TempRoot "dist") `
        --workpath (Join-Path $TempRoot "work") `
        --specpath $TempRoot `
        web_main.py
    if ($LASTEXITCODE -ne 0) { throw "Web EXE build failed with exit code: $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $TempRoot "dist\FbaTrackerWeb.exe") -Destination $OutputExe -Force
}
finally { Pop-Location }

Remove-Item -LiteralPath $TempRoot -Recurse -Force
Write-Host "Web EXE created: $OutputExe"
