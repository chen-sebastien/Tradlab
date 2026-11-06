$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$localPath = Join-Path $projectRoot '.local'
if (-not (Test-Path (Join-Path $localPath 'supervisor.json'))) { Write-Host '沒有本專案的執行紀錄。'; exit 0 }
Set-Content -LiteralPath (Join-Path $localPath 'stop') -Value 'requested'
for ($attempt=0; $attempt -lt 60; $attempt++) {
    if (-not (Test-Path (Join-Path $localPath 'supervisor.json'))) { Write-Host '本專案服務已停止。'; exit 0 }
    Start-Sleep -Milliseconds 500
}
throw 'Supervisor 未回應；未任意終止其他程序。請檢查 .local 紀錄與 README。'
