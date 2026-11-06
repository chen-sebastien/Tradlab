$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) { throw '請先執行 scripts\setup.ps1' }
if (Test-Path '.local\supervisor.json') { throw '已有啟動紀錄。先執行 stop-dev.ps1；若非正常中斷，請依 README 復原。' }
New-Item -ItemType Directory -Path '.local' -Force | Out-Null
Remove-Item -LiteralPath '.local\stop' -ErrorAction SilentlyContinue
Start-Process -FilePath $pythonExe -ArgumentList '-m','scripts.supervisor' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput '.local\supervisor.out.log' -RedirectStandardError '.local\supervisor.err.log' | Out-Null
$ready = $false
for ($attempt=0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $health=Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 1; if ($health.ok) {$ready=$true;break} } catch {}
}
if (-not $ready) { throw '服務未就緒，請檢查 .local 下各服務 log。' }
$secrets = Get-Content '.local\secrets.json' -Raw | ConvertFrom-Json
Set-Content -LiteralPath '.local\client-token.txt' -Value $secrets.client -NoNewline
Write-Host 'TradingLab: http://127.0.0.1:8765'
Write-Host '請在瀏覽器貼上以下本機權杖（不要分享）：'
Write-Host $secrets.client
