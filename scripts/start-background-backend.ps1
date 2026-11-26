<#
.SYNOPSIS
    Start or reuse the backend after verifying the selected existing data store.

.DESCRIPTION
    Used by "TickStock After Close". Reuse requires the same application version,
    data directory, and working after-close scheduler. Never stop another process.
#>
[CmdletBinding()]
param([string]$DataDir)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$LogDir = Join-Path $env:LOCALAPPDATA 'NanachiTaiwanStockPanel'
$Log = Join-Path $LogDir 'after-close-task.log'

function Write-TaskLog([string]$Message) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Add-Content -LiteralPath $Log -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
}

try {
    if ([string]::IsNullOrWhiteSpace($DataDir)) {
        throw 'Missing -DataDir. Reinstall the task after previewing the updated settings.'
    }
    $Uv = Get-Command 'uv.exe' -CommandType Application -ErrorAction Stop | Select-Object -First 1
    Write-TaskLog 'Checking the selected store and the background backend.'
    Push-Location $BackendDir
    try {
        # Release the log between messages: the backend may run until logout.
        & $Uv.Source run --project $BackendDir python -m app.after_close_launcher --data-dir $DataDir |
            ForEach-Object { Write-TaskLog $_ }
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    Write-TaskLog "background launcher exited with code $code"
    exit $code
}
catch {
    Write-TaskLog "failed: $($_.Exception.Message)"
    exit 1
}
