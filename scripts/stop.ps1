$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$processFile = Join-Path $projectRoot '.local\processes.json'
if (-not (Test-Path -LiteralPath $processFile)) { Write-Host 'No saved BriefForge processes.'; return }
$saved = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
foreach ($processId in @($saved.worker,$saved.server)) {
    $running = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if ($running -and $running.ExecutablePath -like "$projectRoot\*" -and $running.CommandLine -match 'briefforge.cli') {
        Stop-Process -Id $processId
    }
}
Write-Host 'BriefForge application processes stopped. Database and files are retained.'
