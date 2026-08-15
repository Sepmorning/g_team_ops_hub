$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$MaintenanceExe = Join-Path $ProjectRoot "GTeamOpsMaintenance.exe"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DataDir = Join-Path $ProjectRoot "data"

if (Test-Path -LiteralPath $MaintenanceExe) {
    & $MaintenanceExe --data-dir $DataDir @args
}
elseif (Test-Path -LiteralPath $Python) {
    & $Python -m g_team_ops.maintenance --data-dir $DataDir @args
}
else {
    throw "维护程序和项目Python环境均不存在"
}

exit $LASTEXITCODE
