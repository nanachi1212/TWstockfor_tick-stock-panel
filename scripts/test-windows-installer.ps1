param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$WorkRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) 'release-assets\installer-smoke'),
    [string]$SeedAuditPath = '',
    [string]$PyInstallerDist = '',
    [string]$PrivacyReport = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$work = [System.IO.Path]::GetFullPath($WorkRoot)
$releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'release-assets'))
if (-not $work.StartsWith($releaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Smoke work root must stay under $releaseRoot"
}

$installDir = Join-Path $work 'App'
$profileRoot = Join-Path $work 'Profile'
$localAppData = Join-Path $profileRoot 'LocalAppData'
$roamingAppData = Join-Path $profileRoot 'RoamingAppData'
$tempDir = Join-Path $profileRoot 'Temp'
$resultPath = Join-Path $work 'smoke-result.json'
if (-not $PrivacyReport) {
    $PrivacyReport = Join-Path $work 'release-privacy-audit.json'
}
foreach ($path in @($work, $localAppData, $roamingAppData, $tempDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

if (Get-NetTCPConnection -LocalPort 3018 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 3018 is already in use; refusing to stop an unrelated process.'
}

function Invoke-JsonApi {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet('GET', 'POST')][string]$Method = 'GET',
        [string]$Body = '',
        [int]$TimeoutSec = 30
    )
    $params = @{
        Uri = "http://127.0.0.1:3018$Path"
        Method = $Method
        TimeoutSec = $TimeoutSec
        ErrorAction = 'Stop'
    }
    if ($Body) {
        $params.ContentType = 'application/json'
        $params.Body = $Body
    }
    Invoke-RestMethod @params
}

function Start-IsolatedApp {
    param([bool]$Offline)
    $exe = Join-Path $installDir 'NanachiStockPanel.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Installed executable missing: $exe"
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $exe
    $startInfo.WorkingDirectory = $installDir
    $startInfo.UseShellExecute = $false
    $startInfo.Environment['LOCALAPPDATA'] = $localAppData
    $startInfo.Environment['APPDATA'] = $roamingAppData
    $startInfo.Environment['DATA_DIR'] = Join-Path $localAppData 'NanachiStockPanel\data'
    $startInfo.Environment['TEMP'] = $tempDir
    $startInfo.Environment['TMP'] = $tempDir
    $startInfo.Environment['NO_PROXY'] = '127.0.0.1,localhost'
    @($startInfo.Environment.Keys) | Where-Object {
        $_ -match '(?i)(API[_-]?KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION|WEBHOOK|FINMIND|TELEGRAM|LINE_CHANNEL)'
    } | ForEach-Object {
        [void]$startInfo.Environment.Remove($_)
    }
    if ($Offline) {
        $startInfo.Environment['HTTP_PROXY'] = 'http://127.0.0.1:9'
        $startInfo.Environment['HTTPS_PROXY'] = 'http://127.0.0.1:9'
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            throw "Desktop app exited before health check (exit $($process.ExitCode))."
        }
        try {
            $health = Invoke-JsonApi -Path '/health'
            if ($health.status -eq 'ok') {
                return $process
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw 'Desktop app health check timed out.'
}

function Stop-IsolatedApp {
    param([System.Diagnostics.Process]$Process)
    if ($Process.HasExited) { return }
    [void]$Process.CloseMainWindow()
    # On CI, WebView2 can outlive the backend shutdown log while it releases
    # the isolated profile. Keep this a graceful close and wait for completion.
    if (-not $Process.WaitForExit(60000)) {
        throw 'Desktop app did not exit after a normal window close.'
    }
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort 3018 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Desktop app left an orphan listener on port 3018.'
    }
}

$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nanachi 的台股監控看板.lnk'
$startMenuShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Nanachi 的台股監控看板\Nanachi 的台股監控看板.lnk'
$installerArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$installDir`"")
$installProcess = Start-Process -FilePath $installer -ArgumentList $installerArgs -PassThru -Wait -WindowStyle Hidden
if ($installProcess.ExitCode -ne 0) {
    throw "Installer failed with exit code $($installProcess.ExitCode)."
}
if (-not (Test-Path -LiteralPath $desktopShortcut -PathType Leaf)) {
    throw "Desktop shortcut missing: $desktopShortcut"
}
if (-not (Test-Path -LiteralPath $startMenuShortcut -PathType Leaf)) {
    throw "Start Menu shortcut missing: $startMenuShortcut"
}

$offlineProcess = $null
$onlineProcess = $null
try {
    $offlineProcess = Start-IsolatedApp -Offline $true
    $history = Invoke-JsonApi -Path '/api/taiwan/history-status'
    if (-not $history.has_data -or $history.trading_days -lt 20) {
        throw 'Bundled seed market history is not available in the clean profile.'
    }
    $stock = Invoke-JsonApi -Path '/api/taiwan/stocks/2330.TWSE' -TimeoutSec 120
    $screener = Invoke-JsonApi -Path '/api/taiwan/screener/run' -Method POST -Body '{}'
    $events = Invoke-JsonApi -Path '/api/taiwan/events?limit=5' -TimeoutSec 120
    $selection = Invoke-JsonApi -Path '/api/taiwan/selection-review/snapshots'
    $social = Invoke-JsonApi -Path '/api/taiwan/social-sentiment/history?limit=30'
    $watchlist = Invoke-JsonApi -Path '/api/watchlist'
    $alerts = Invoke-JsonApi -Path '/api/alerts'
    $rules = Invoke-JsonApi -Path '/api/monitor-rules'
    $profiles = Invoke-JsonApi -Path '/api/settings/ai-key-profiles'
    $settings = Invoke-JsonApi -Path '/api/settings'

    if ($selection.Count -ne 0 -or $social.items.Count -ne 0) {
        throw 'Clean profile contains personal research or social history.'
    }
    if ($watchlist.symbols.Count -ne 0 -or $alerts.total -ne 0 -or $rules.rules.Count -ne 0) {
        throw 'Clean profile contains watchlist, alert, or monitor-rule data.'
    }
    if ($profiles.profiles.Count -ne 0 -or $settings.has_ai_key -or $settings.has_finmind_token) {
        throw 'Clean profile contains AI or FinMind credentials.'
    }
    if (-not $stock.symbol -or $null -eq $screener.items -or $null -eq $events.events) {
        throw 'One or more product smoke endpoints returned an invalid response.'
    }
    Stop-IsolatedApp -Process $offlineProcess
    $offlineProcess = $null

    $sentinel = Join-Path $localAppData 'NanachiStockPanel\data\user_data\upgrade-preserve-sentinel.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sentinel) | Out-Null
    Set-Content -LiteralPath $sentinel -Value '{"empty":true}' -Encoding utf8NoBOM
    $upgradeProcess = Start-Process -FilePath $installer -ArgumentList $installerArgs -PassThru -Wait -WindowStyle Hidden
    if ($upgradeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $sentinel)) {
        throw 'Upgrade install did not preserve the isolated user-data sentinel.'
    }

    $onlineProcess = Start-IsolatedApp -Offline $false
    # The pinned public CI seed can trail the current market by multiple weeks.
    # Keep the real end-to-end refresh and allow the official endpoints enough
    # time to serve every missing trading day plus readiness evidence.
    $incremental = Invoke-JsonApi -Path '/api/taiwan/bootstrap/update-latest' -Method POST -TimeoutSec 600
    if (-not $incremental.ok -or [int]$incremental.dates_fetched -gt 60) {
        throw 'Incremental refresh failed or attempted an unexpected historical backfill.'
    }
    Stop-IsolatedApp -Process $onlineProcess
    $onlineProcess = $null

    $privacyArgs = @(
        (Join-Path $repoRoot 'scripts\release_privacy_audit.py'),
        '--artifact', $installDir,
        '--artifact', $installer,
        '--output', $PrivacyReport
    )
    if ($SeedAuditPath) { $privacyArgs += @('--seed', $SeedAuditPath) }
    if ($PyInstallerDist) { $privacyArgs += @('--artifact', $PyInstallerDist) }
    & (Join-Path $repoRoot 'backend\.venv\Scripts\python.exe') @privacyArgs
    if ($LASTEXITCODE -ne 0) { throw 'Final artifact privacy audit failed.' }

    $result = [ordered]@{
        result = 'PASS'
        offline_startup = 'PASS'
        clean_install = 'PASS'
        desktop_shortcut = 'PASS'
        start_menu_shortcut = 'PASS'
        dashboard_seed_history = 'PASS'
        stock_detail = 'PASS'
        screener = 'PASS'
        selection_review_empty = 'PASS'
        event_center = 'PASS'
        social_history_empty = 'PASS'
        incremental_refresh = 'PASS'
        clean_profile_privacy = 'PASS'
        orphan_processes = 0
        data_as_of = $history.latest_date
        dates_fetched = [int]$incremental.dates_fetched
    }
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding utf8NoBOM
} finally {
    foreach ($process in @($offlineProcess, $onlineProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            [void]$process.CloseMainWindow()
            if (-not $process.WaitForExit(5000)) {
                Stop-Process -Id $process.Id -Force
                [void]$process.WaitForExit(5000)
            }
        }
    }
}

$uninstaller = Join-Path $installDir 'unins000.exe'
if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    throw 'Uninstaller missing after clean install.'
}
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -PassThru -Wait -WindowStyle Hidden
if ($uninstallProcess.ExitCode -ne 0) {
    throw "Uninstaller failed with exit code $($uninstallProcess.ExitCode)."
}
if (-not (Test-Path -LiteralPath $sentinel -PathType Leaf)) {
    throw 'Default uninstall removed user data.'
}

Get-Content -LiteralPath $resultPath -Raw
