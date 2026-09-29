param(
    [switch]$SkipFrontendBuild,
    [switch]$SkipSmoke,
    [string]$SmokeWorkRoot = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$seedZip = Join-Path $repoRoot 'release-assets\release-seed\release-seed.zip'
$seedAuditRoot = Get-ChildItem -LiteralPath (Join-Path $repoRoot 'release-assets\release-seed') -Directory -Filter 'audit-staging-*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not (Test-Path -LiteralPath $seedZip -PathType Leaf)) {
    & (Join-Path $backendDir '.venv\Scripts\python.exe') (Join-Path $repoRoot 'scripts\build_release_seed.py')
    if ($LASTEXITCODE -ne 0) { throw 'Release seed build failed.' }
    $seedAuditRoot = Get-ChildItem -LiteralPath (Split-Path -Parent $seedZip) -Directory -Filter 'audit-staging-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if ($null -eq $seedAuditRoot) {
    throw 'Release seed audit staging is missing.'
}

if (-not $SkipFrontendBuild) {
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally {
        Pop-Location
    }
}

Push-Location $backendDir
try {
    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    $webviewModule = Join-Path $backendDir '.venv\Lib\site-packages\webview\__init__.py'
    if (-not (Test-Path -LiteralPath $webviewModule -PathType Leaf)) {
        uv sync --extra desktop --extra legacy-cpu
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $backendDir '.venv\Scripts\pyinstaller.exe'))) {
        uv pip install pyinstaller
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    }
    & $python -m PyInstaller ..\packaging\tickflow.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
} finally {
    Pop-Location
}

$distDir = Join-Path $backendDir 'dist\NanachiStockPanel'
$appExe = Join-Path $distDir 'NanachiStockPanel.exe'
if (-not (Test-Path -LiteralPath $appExe -PathType Leaf)) {
    throw "PyInstaller output missing: $appExe"
}

$isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$iscc = if ($isccCommand) {
    $isccCommand.Source
} else {
    $localCompiler = Join-Path $repoRoot 'release-assets\tools\Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $localCompiler -PathType Leaf) {
        $localCompiler
    } else {
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
    }
}
if (-not (Test-Path -LiteralPath $iscc -PathType Leaf)) {
    throw 'Inno Setup 6 compiler is not installed.'
}
$releaseTag = (Get-Content -LiteralPath (Join-Path $repoRoot 'VERSION') -Raw).Trim()
$version = $releaseTag.TrimStart('v')
& $iscc "/DMyAppVersion=$version" (Join-Path $repoRoot 'packaging\tickflow.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }

$installer = Join-Path $repoRoot 'packaging\Output\NanachiStockPanel-Setup-x64.exe'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Installer output missing: $installer"
}
if (-not $SmokeWorkRoot) {
    $SmokeWorkRoot = Join-Path $repoRoot 'release-assets\installer-smoke'
}
$privacyReport = Join-Path $SmokeWorkRoot 'release-privacy-audit.json'
if (-not $SkipSmoke) {
    & (Join-Path $repoRoot 'scripts\test-windows-installer.ps1') `
        -InstallerPath $installer `
        -WorkRoot $SmokeWorkRoot `
        -SeedAuditPath $seedAuditRoot.FullName `
        -PyInstallerDist $distDir `
        -PrivacyReport $privacyReport
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer smoke failed.' }
}

$distBytes = (Get-ChildItem -LiteralPath $distDir -File -Recurse | Measure-Object -Property Length -Sum).Sum
$seedBytes = (Get-Item -LiteralPath $seedZip).Length
$installerInfo = Get-Item -LiteralPath $installer
$buildResult = [ordered]@{
    result = if ($SkipSmoke) { 'BUILT_NOT_SMOKED' } else { 'INSTALLER_READY_UNSIGNED' }
    installer_path = $installerInfo.FullName
    installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    installer_bytes = $installerInfo.Length
    app_runtime_bytes = [int64]$distBytes
    seed_compressed_bytes = [int64]$seedBytes
    seed_manifest = (Join-Path $seedAuditRoot.FullName 'manifest.json')
    privacy_report = $privacyReport
    signing_status = 'unsigned'
}
$resultPath = Join-Path $repoRoot 'release-assets\installer-build-result.json'
$buildResult | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding utf8NoBOM
$buildResult | ConvertTo-Json -Depth 5
