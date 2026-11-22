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

function Test-BackendPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $BackendPort, $null, $null)
        return $pending.AsyncWaitHandle.WaitOne(1000) -and $client.Connected
    }
    catch { return $false }
    finally { $client.Close() }
}

try {
    if (Test-BackendPort) {
        Write-TaskLog "backend already running on port $BackendPort; nothing to do"
        exit 0
    }
    $Uv = Get-Command 'uv.exe' -ErrorAction Stop
    Write-TaskLog "starting background backend on port $BackendPort"
    Push-Location $BackendDir
    try {
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
