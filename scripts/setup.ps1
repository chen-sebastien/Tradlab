param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw '請先安裝 Node.js 24 LTS。' }
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { & uv venv --python 3.12 .venv }
    elseif ($Python) { & $Python -m venv .venv }
    else { & py -3.12 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw '建立 Python 3.12 環境失敗。請安裝 Python 3.12 或 uv。' }
}
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Get-Command uv -ErrorAction SilentlyContinue) { & uv pip sync requirements.lock --python $pythonExe }
else { & $pythonExe -m pip install -r requirements.lock }
if ($LASTEXITCODE -ne 0) { throw 'Python 依賴安裝失敗' }
& $pythonExe -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration 失敗' }
& $pythonExe -m scripts.init_db
if ($LASTEXITCODE -ne 0) { throw '初始化失敗' }
Push-Location frontend
try { & npm.cmd ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm ci 失敗' }; & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw '前端建置失敗' } } finally { Pop-Location }
$secrets = Get-Content '.local\secrets.json' -Raw | ConvertFrom-Json
Set-Content -LiteralPath '.local\client-token.txt' -Value $secrets.client -NoNewline
Write-Host 'TradingLab 已完成安裝。請執行 scripts\start-dev.ps1。'
