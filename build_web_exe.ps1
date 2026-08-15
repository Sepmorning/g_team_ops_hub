$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path $ProjectRoot ".build-g-team-ops"
$OutputExe = Join-Path $ProjectRoot "GTeamOpsHub.exe"
$MaintenanceExe = Join-Path $ProjectRoot "GTeamOpsMaintenance.exe"
$Templates = Join-Path $ProjectRoot "g_team_ops\web\templates"
$Static = Join-Path $ProjectRoot "g_team_ops\web\static"
$Migrations = Join-Path $ProjectRoot "g_team_ops\db\migrations"

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
        --name "GTeamOpsHub" `
        --add-data "$Templates;g_team_ops\web\templates" `
        --add-data "$Static;g_team_ops\web\static" `
        --add-data "$Migrations;g_team_ops\db\migrations" `
        --distpath (Join-Path $TempRoot "dist") `
        --workpath (Join-Path $TempRoot "work") `
        --specpath $TempRoot `
        web_main.py
    if ($LASTEXITCODE -ne 0) { throw "Web EXE build failed with exit code: $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $TempRoot "dist\GTeamOpsHub.exe") -Destination $OutputExe -Force
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name "GTeamOpsMaintenance" `
        --add-data "$Migrations;g_team_ops\db\migrations" `
        --distpath (Join-Path $TempRoot "dist-maintenance") `
        --workpath (Join-Path $TempRoot "work-maintenance") `
        --specpath $TempRoot `
        maintenance_main.py
    if ($LASTEXITCODE -ne 0) { throw "Maintenance EXE build failed with exit code: $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $TempRoot "dist-maintenance\GTeamOpsMaintenance.exe") -Destination $MaintenanceExe -Force
}
finally { Pop-Location }

Remove-Item -LiteralPath $TempRoot -Recurse -Force
Write-Host "Web EXE created: $OutputExe"
Write-Host "Maintenance EXE created: $MaintenanceExe"
