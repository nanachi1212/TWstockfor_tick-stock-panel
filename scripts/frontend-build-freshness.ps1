[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-FrontendBuildInputFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FrontendDir
    )

    $root = (Resolve-Path -LiteralPath $FrontendDir).Path
    $files = [System.Collections.Generic.List[object]]::new()
    $explicitNames = @(
        'index.html',
        'package.json',
        'pnpm-lock.yaml',
        'tsconfig.json',
        'tsconfig.node.json',
        'tsconfig.*.json',
        'vite.config.*',
        'tailwind.config.*',
        'postcss.config.*',
        '.env',
        '.env.*'
    )

    foreach ($pattern in $explicitNames) {
        Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like $pattern } |
            ForEach-Object { $files.Add($_) }
    }

    foreach ($directoryName in @('src', 'public')) {
        $directory = Join-Path $root $directoryName
        if (Test-Path -LiteralPath $directory -PathType Container) {
            Get-ChildItem -LiteralPath $directory -File -Recurse |
                ForEach-Object { $files.Add($_) }
        }
    }

    $unique = @{}
    foreach ($file in $files) {
        $unique[$file.FullName.ToLowerInvariant()] = $file
    }
    return @($unique.Values | Sort-Object FullName)
}

function Test-RepositoryDevelopmentWorkspace {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    return (Test-Path -LiteralPath (Join-Path $Root '.git')) -and
        (Test-Path -LiteralPath (Join-Path $Root 'frontend\package.json') -PathType Leaf)
}

function Get-FrontendBuildManifest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FrontendDir
    )

    $root = (Resolve-Path -LiteralPath $FrontendDir).Path
    $inputs = Get-FrontendBuildInputFiles -FrontendDir $root
    if ($inputs.Count -eq 0) {
        throw "找不到 frontend build input: $root"
    }

    $entries = foreach ($file in $inputs) {
        $relative = $file.FullName.Substring($root.Length).TrimStart('\', '/') -replace '\\', '/'
        [ordered]@{
            path = $relative
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $canonical = ($entries | ForEach-Object { "$($_.path)`t$($_.sha256)" }) -join "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    $digest = [System.Security.Cryptography.SHA256]::Create()
    try {
        $fingerprint = ([System.BitConverter]::ToString($digest.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $digest.Dispose()
    }

    [ordered]@{
        schema = 1
        fingerprint = $fingerprint
        inputs = @($entries)
    }
}

function Test-FrontendBuildCurrent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FrontendDir,
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$ExpectedManifest
    )

    $dist = Join-Path $FrontendDir 'dist'
    $manifestPath = Join-Path $dist 'frontend-build-manifest.json'
    $indexPath = Join-Path $dist 'index.html'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
        return $false
    }

    try {
        $recorded = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        return $recorded.schema -eq $ExpectedManifest.schema -and
            $recorded.fingerprint -eq $ExpectedManifest.fingerprint
    }
    catch {
        return $false
    }
}

function Ensure-FrontendBuildCurrent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FrontendDir,
        [string]$PnpmPath = ''
    )

    $frontendRoot = (Resolve-Path -LiteralPath $FrontendDir).Path
    $expected = Get-FrontendBuildManifest -FrontendDir $frontendRoot
    if (Test-FrontendBuildCurrent -FrontendDir $frontendRoot -ExpectedManifest $expected) {
        return 'current'
    }

    if (-not $PnpmPath) {
        $pnpmCommand = Get-Command 'pnpm.cmd' -ErrorAction SilentlyContinue
        if (-not $pnpmCommand) { $pnpmCommand = Get-Command 'pnpm.exe' -ErrorAction SilentlyContinue }
        if (-not $pnpmCommand) { $pnpmCommand = Get-Command 'pnpm' -ErrorAction Stop }
        $PnpmPath = $pnpmCommand.Source
    }
    if (-not (Test-Path -LiteralPath $PnpmPath -PathType Leaf)) {
        throw "找不到 pnpm，無法更新 frontend/dist: $PnpmPath"
    }

    Write-Host 'frontend/dist 不存在或已過期，執行 pnpm build...'
    Push-Location $frontendRoot
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $PnpmPath 'build' 2>&1 | ForEach-Object { Write-Host $_ }
            $buildExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($buildExitCode -ne 0) {
            throw "Frontend build 失敗，pnpm build exit code: $buildExitCode"
        }
    }
    finally {
        Pop-Location
    }

    $dist = Join-Path $frontendRoot 'dist'
    if (-not (Test-Path -LiteralPath (Join-Path $dist 'index.html') -PathType Leaf)) {
        throw 'Frontend build 完成但 dist/index.html 不存在。'
    }
    $manifestPath = Join-Path $dist 'frontend-build-manifest.json'
    $temporaryManifestPath = "$manifestPath.tmp"
    $expected | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryManifestPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryManifestPath -Destination $manifestPath -Force
    return 'built'
}
