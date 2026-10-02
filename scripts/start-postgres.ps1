param([string]$BinPath = $env:BRIEFFORGE_PG_BIN)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataPath = Join-Path $projectRoot '.local\pgdata'
$binHint = Join-Path $projectRoot '.local\pg-bin.txt'
if (-not (Test-Path -LiteralPath (Join-Path $dataPath 'PG_VERSION'))) { throw 'No BriefForge native cluster found. Use Docker Compose or configure BRIEFFORGE_DATABASE_URL.' }
if (Get-NetTCPConnection -LocalPort 55432 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'PostgreSQL port 55432 is already listening.'; return }
if (-not $BinPath -and (Test-Path -LiteralPath $binHint)) { $BinPath = (Get-Content -LiteralPath $binHint -Raw).Trim() }
if (-not $BinPath) { throw 'Set BRIEFFORGE_PG_BIN to the PostgreSQL 16 bin directory.' }
$pgCtl = Join-Path $BinPath 'pg_ctl.exe'
if (-not (Test-Path -LiteralPath $pgCtl)) { throw 'pg_ctl.exe was not found in the specified directory.' }
$logPath = Join-Path $projectRoot '.local\postgres.log'
$arguments = '-D "{0}" -l "{1}" -w start' -f $dataPath,$logPath
$process = Start-Process -FilePath $pgCtl -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'PostgreSQL startup failed; inspect .local/postgres.log.' }
Write-Host 'BriefForge PostgreSQL is running on localhost:55432.'
