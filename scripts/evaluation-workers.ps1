param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot '.local\evaluation-workers.json'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($processId in $saved.processIds) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
        if ($process -and $process.ExecutablePath -eq $pythonExe -and $process.CommandLine -match 'briefforge.cli.*worker') {
            if ($Stop) { Stop-Process -Id $processId } else { throw 'Owned evaluation workers are already running.' }
        }
    }
}
if ($Stop) { Write-Host 'Owned auxiliary evaluation workers stopped; application worker is unchanged.'; return }
$env:BRIEFFORGE_DATA_DIR = Join-Path $projectRoot '.local'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:BRIEFFORGE_DATABASE_URL) {
    $pgPassword = [Uri]::EscapeDataString((Get-Content -LiteralPath (Join-Path $projectRoot '.local\pg-password.txt') -Raw).Trim())
    $env:BRIEFFORGE_DATABASE_URL = "postgresql+psycopg://briefforge:${pgPassword}@127.0.0.1:55432/briefforge"
}
$userKey = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')
if ($userKey) { $env:OPENROUTER_API_KEY = $userKey }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ownedIds = @()
foreach ($index in 1..2) {
    $prefix = Join-Path $projectRoot ".local\evaluation-worker-$stamp-$index"
    $process = Start-Process -FilePath $pythonExe -ArgumentList @('-m','briefforge.cli','worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$prefix.stdout.log" -RedirectStandardError "$prefix.stderr.log"
    $ownedIds += $process.Id
}
@{processIds=$ownedIds;started=$stamp;purpose='Two auxiliary workers for the frozen three-run benchmark; same durable budget ledger.'} | ConvertTo-Json | Set-Content -LiteralPath $pidFile
Write-Host 'Two auxiliary workers started. Each research still permits at most three parallel role tasks.'
