<#
.SYNOPSIS
    Start the backend in the background unless one already answers on 127.0.0.1:3018.

.DESCRIPTION
    Used by the "TickStock After Close" scheduled task. A running backend (desktop app or
    autostart) already runs the 16:30 / 21:30 / 23:00 jobs, so this exits at once. Otherwise it
    runs the same backend as install-autostart.ps1 in the foreground of this hidden console;
    its startup catch-up does the after-close work and its scheduler covers the evening runs.
    Opening the desktop app afterwards reuses this backend.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$BackendPort = 3018   # same as app.desktop _BASE_PORT
$LogDir = Join-Path $env:LOCALAPPDATA 'NanachiTaiwanStockPanel'
$Log = Join-Path $LogDir 'after-close-task.log'

function Write-TaskLog([string]$Message) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Add-Content -LiteralPath $Log -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
}

try {
    $Uv = Get-Command 'uv.exe' -ErrorAction Stop
    Push-Location $BackendDir
    try {
        $portStatus = & $Uv.Source run --project $BackendDir python -c 'from app.desktop import background_port_status; print(background_port_status())'
        if ($LASTEXITCODE -ne 0) { throw "Backend identity probe failed (exit $LASTEXITCODE)." }
        if ($portStatus -eq 'ready') {
            Write-TaskLog "verified backend already running on port $BackendPort; nothing to do"
            exit 0
        }
        if ($portStatus -ne 'free') { throw "Port $BackendPort is occupied without a verified project backend." }
        Write-TaskLog "starting background backend on port $BackendPort"
        & $Uv.Source run --project $BackendDir python -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    Write-TaskLog "background backend exited with code $code"
    exit $code
}
catch {
    Write-TaskLog "failed: $($_.Exception.Message)"
    exit 1
}
