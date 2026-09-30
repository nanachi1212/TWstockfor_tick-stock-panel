$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '..\frontend-build-freshness.ps1')

function Assert-Equal {
    param([object]$Actual, [object]$Expected, [string]$Message)
    if ($Actual -ne $Expected) { throw "$Message. Actual: $Actual; Expected: $Expected" }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "nanachi-frontend-freshness-$([guid]::NewGuid())"
$frontend = Join-Path $testRoot 'frontend'
$src = Join-Path $frontend 'src'
$dist = Join-Path $frontend 'dist'
$fakePnpm = Join-Path $testRoot 'pnpm.cmd'
$buildMarker = Join-Path $testRoot 'build-called.txt'
New-Item -ItemType Directory -Path $src, $dist -Force | Out-Null
Set-Content -LiteralPath (Join-Path $frontend 'index.html') -Value '<div id="root"></div>'
Set-Content -LiteralPath (Join-Path $frontend 'package.json') -Value '{}'
Set-Content -LiteralPath (Join-Path $frontend 'pnpm-lock.yaml') -Value 'lockfileVersion: 9'
Set-Content -LiteralPath (Join-Path $frontend 'tsconfig.json') -Value '{}'
Set-Content -LiteralPath (Join-Path $frontend 'vite.config.ts') -Value 'export default {}'
Set-Content -LiteralPath (Join-Path $src 'main.tsx') -Value 'export const value = 1'
Set-Content -LiteralPath $fakePnpm -Value "@echo build > `"$buildMarker`"`r`n@echo ^<html^> ^<body^>built^</body^>^</html^> > `"$dist\index.html`"`r`nexit /b 0"

try {
    $manifest = Get-FrontendBuildManifest -FrontendDir $frontend
    Assert-True ($manifest.fingerprint.Length -eq 64) 'fingerprint should be SHA-256'
    Assert-True (-not (Test-FrontendBuildCurrent -FrontendDir $frontend -ExpectedManifest $manifest)) 'missing dist manifest should be stale'

    Assert-Equal (Ensure-FrontendBuildCurrent -FrontendDir $frontend -PnpmPath $fakePnpm) 'built' 'stale build should run pnpm'
    Assert-True (Test-Path -LiteralPath $buildMarker) 'build command was not invoked'
    Assert-True (Test-FrontendBuildCurrent -FrontendDir $frontend -ExpectedManifest $manifest) 'built dist should be current'
    Assert-Equal (Ensure-FrontendBuildCurrent -FrontendDir $frontend -PnpmPath (Join-Path $testRoot 'missing-pnpm.cmd')) 'current' 'current build should not invoke pnpm'

    Set-Content -LiteralPath (Join-Path $src 'main.tsx') -Value 'export const value = 2'
    $changedManifest = Get-FrontendBuildManifest -FrontendDir $frontend
    Assert-True ($changedManifest.fingerprint -ne $manifest.fingerprint) 'source change should alter fingerprint'
    Assert-True (-not (Test-FrontendBuildCurrent -FrontendDir $frontend -ExpectedManifest $changedManifest)) 'source change should make dist stale'

    Set-Content -LiteralPath (Join-Path $frontend 'pnpm-lock.yaml') -Value 'lockfileVersion: 10'
    $lockChangedManifest = Get-FrontendBuildManifest -FrontendDir $frontend
    Assert-True ($lockChangedManifest.fingerprint -ne $changedManifest.fingerprint) 'lockfile change should alter fingerprint'

    Assert-True (-not (Test-RepositoryDevelopmentWorkspace -Root $testRoot)) 'frozen/install root must not be a development workspace'
    New-Item -ItemType File -Path (Join-Path $testRoot '.git') | Out-Null
    Assert-True (Test-RepositoryDevelopmentWorkspace -Root $testRoot) 'repository root should be a development workspace'

    Set-Content -LiteralPath $fakePnpm -Value '@exit /b 7'
    $failed = $false
    try { Ensure-FrontendBuildCurrent -FrontendDir $frontend -PnpmPath $fakePnpm | Out-Null } catch { $failed = $true }
    Assert-True $failed 'build failure should stop startup'

    Write-Host 'PASS: frontend build freshness tests'
}
finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
