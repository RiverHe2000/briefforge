param([int]$Port = 8788)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run uv sync --extra dev first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw 'Build frontend first: pnpm --dir frontend build' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is already in use. Existing service was not changed." }
$localData = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localData | Out-Null
$env:BRIEFFORGE_DATA_DIR = $localData
$pgSecret = Join-Path $localData 'pg-password.txt'
if (-not $env:BRIEFFORGE_DATABASE_URL -and (Test-Path -LiteralPath $pgSecret)) {
    & (Join-Path $PSScriptRoot 'start-postgres.ps1')
    $pgPassword = [Uri]::EscapeDataString((Get-Content -LiteralPath $pgSecret -Raw).Trim())
    $env:BRIEFFORGE_DATABASE_URL = "postgresql+psycopg://briefforge:${pgPassword}@127.0.0.1:55432/briefforge"
}
$userKey = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')
if ($userKey) { $env:OPENROUTER_API_KEY = $userKey }
$workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','briefforge.cli','worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localData 'worker.stdout.log') -RedirectStandardError (Join-Path $localData 'worker.stderr.log')
try {
    Write-Host "BriefForge: http://127.0.0.1:$Port"
    & $pythonExe -m briefforge.cli serve --port $Port
} finally {
    if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id -ErrorAction SilentlyContinue }
}
