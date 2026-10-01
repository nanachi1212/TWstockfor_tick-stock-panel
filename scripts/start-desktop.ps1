[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$FallbackLogDir = Join-Path $env:LOCALAPPDATA 'NanachiTaiwanStockPanel'
$FallbackLog = Join-Path $FallbackLogDir 'desktop-launcher.log'

try {
    . (Join-Path $PSScriptRoot 'frontend-build-freshness.ps1')
    if (Test-RepositoryDevelopmentWorkspace -Root $Root) {
        Ensure-FrontendBuildCurrent -FrontendDir $FrontendDir | Out-Null
    }

    $Uv = Get-Command 'uv.exe' -ErrorAction Stop
    Push-Location $BackendDir
    try {
        & $Uv.Source run --project $BackendDir --extra desktop python -m app.desktop
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop entry 結束，exit code: $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    New-Item -ItemType Directory -Path $FallbackLogDir -Force | Out-Null
    $message = "$(Get-Date -Format o) $($_.Exception.Message)"
    Add-Content -LiteralPath $FallbackLog -Value $message -Encoding UTF8
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Nanachi 台股看板啟動失敗。`n`n$($_.Exception.Message)`n`n記錄：$FallbackLog",
        'Nanachi 台股看板',
        'OK',
        'Error'
    ) | Out-Null
    exit 1
}
