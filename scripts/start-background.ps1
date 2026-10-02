param([int]$Port = 8788)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$localData = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localData | Out-Null
$env:BRIEFFORGE_DATA_DIR = $localData
$env:PYTHONIOENCODING = 'utf-8'
$pgSecret = Join-Path $localData 'pg-password.txt'
if (-not $env:BRIEFFORGE_DATABASE_URL -and (Test-Path -LiteralPath $pgSecret)) {
    & (Join-Path $PSScriptRoot 'start-postgres.ps1')
    $pgPassword = [Uri]::EscapeDataString((Get-Content -LiteralPath $pgSecret -Raw).Trim())
    $env:BRIEFFORGE_DATABASE_URL = "postgresql+psycopg://briefforge:${pgPassword}@127.0.0.1:55432/briefforge"
}
# Fresh shells receive the current user-scoped key. This also avoids inheriting
# an expired key from a long-running editor when the user updated the setting.
$userKey = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')
if ($userKey) { $env:OPENROUTER_API_KEY = $userKey }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is already in use. Existing service was not changed." }
$worker = Start-Process -FilePath $pythonExe -ArgumentList @('-m','briefforge.cli','worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localData 'worker.stdout.log') -RedirectStandardError (Join-Path $localData 'worker.stderr.log')
$server = Start-Process -FilePath $pythonExe -ArgumentList @('-m','briefforge.cli','serve','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localData 'server.stdout.log') -RedirectStandardError (Join-Path $localData 'server.stderr.log')
@{worker=$worker.Id;server=$server.Id;port=$Port} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localData 'processes.json')
Write-Host "BriefForge started at http://127.0.0.1:$Port"

