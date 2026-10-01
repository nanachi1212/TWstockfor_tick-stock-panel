[CmdletBinding()]
param(
    [ValidateSet('Fast', 'Auto', 'Full', 'Live')]
    [string]$Mode = 'Auto',

    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Get-GitNames {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $names = @(& git -C $repoRoot @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    return @(
        $names |
            ForEach-Object { ([string]$_).Trim().Replace('\', '/') } |
            Where-Object { $_ }
    )
}

function Get-ChangedPaths {
    $paths = [System.Collections.Generic.List[string]]::new()

    if ((Get-GitNames -Arguments @('rev-parse', '--verify', 'origin/main')).Count -gt 0) {
        Get-GitNames -Arguments @('diff', '--name-only', 'origin/main...HEAD') | ForEach-Object { $paths.Add($_) }
    }

    Get-GitNames -Arguments @('diff', '--name-only') | ForEach-Object { $paths.Add($_) }
    Get-GitNames -Arguments @('diff', '--cached', '--name-only') | ForEach-Object { $paths.Add($_) }
    Get-GitNames -Arguments @('ls-files', '--others', '--exclude-standard') | ForEach-Object { $paths.Add($_) }

    return @($paths | Sort-Object -Unique)
}

function Test-PathPattern {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string[]]$Patterns
    )

    foreach ($pattern in $Patterns) {
        if ($Path -match $pattern) {
            return $true
        }
    }

    return $false
}

function Get-ChangeClassification {
    param(
        [Parameter(Mandatory)]
        [string[]]$Paths
    )

    $backend = @($Paths | Where-Object { $_ -match '^backend/' })
    $frontend = @($Paths | Where-Object { $_ -match '^frontend/' })
    $documentationOnly = @(
        $Paths | Where-Object {
            $_ -match '^(README\.md|AGENTS\.md|CLAUDE\.md|CONTRIBUTING\.md|docs/|操作說明書\.md$)'
        }
    )

    $fullPatterns = @(
        '(^|/)pyproject\.toml$',
        '(^|/)requirements[^/]*$',
        '(^|/)(uv\.lock|package\.json|pnpm-lock\.yaml|yarn\.lock|package-lock\.json)$',
        '^\.github/',
        '^scripts/ci\.ps1$',
        '(^|/)(tsconfig[^/]*\.json|vite\.config\.[^/]+|vitest\.config\.[^/]+)$',
        '(^|/)(pytest\.ini|tox\.ini|conftest\.py)$',
        '(^|/)(migrations?|alembic)(/|$)',
        '^backend/app/api/',
        '^backend/app/(models|schemas)/',
        '^frontend/src/lib/(api|queryKeys)\.',
        '^frontend/src/extensions/types\.',
        '^docs/.*(contract|schema)',
        '^(Dockerfile|docker-compose\.yml|dev\.ps1)$',
        '^packaging/'
    )

    $requiresFull = @($Paths | Where-Object { Test-PathPattern -Path $_ -Patterns $fullPatterns }).Count -gt 0
    $knownPath = @(
        $Paths | Where-Object {
            $_ -match '^(backend|frontend|scripts|\.github|packaging|docs|specs|data)/' -or
            $_ -match '^(README\.md|AGENTS\.md|CLAUDE\.md|CONTRIBUTING\.md|操作說明書\.md|Dockerfile|docker-compose\.yml|dev\.ps1|VERSION|tiers\.yaml)$'
        }
    )
    $unknown = @($Paths | Where-Object { $_ -notin $knownPath -and $_ -notin $documentationOnly })

    if ($unknown.Count -gt 0) {
        $requiresFull = $true
    }

    [pscustomobject]@{
        Backend           = $backend.Count -gt 0
        Frontend          = $frontend.Count -gt 0
        RequiresFull      = $requiresFull
        DocumentationOnly = $Paths.Count -gt 0 -and $documentationOnly.Count -eq $Paths.Count
        BackendPaths      = $backend
        FrontendPaths     = $frontend
        UnknownPaths      = $unknown
    }
}

function Get-TestTargets {
    param(
        [Parameter(Mandatory)]
        [string[]]$Paths,

        [Parameter(Mandatory)]
        [string]$Root,

        [Parameter(Mandatory)]
        [string]$TestRoot,

        [Parameter(Mandatory)]
        [string]$SourcePrefix,

        [Parameter(Mandatory)]
        [string]$TestPathPrefix
    )

    $targets = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $testRootPath = Join-Path $Root $TestRoot

    foreach ($path in $Paths) {
        if ($path -notmatch "^$TestPathPrefix") {
            continue
        }

        if ($path -match '\.(test|spec)\.[^/]+$' -or $path -match '/tests/[^/]+\.py$') {
            [void]$targets.Add(($path -replace "^$SourcePrefix/", '').Replace('/', '\'))
            continue
        }

        $stem = [System.IO.Path]::GetFileNameWithoutExtension($path)
        if (-not $stem) {
            continue
        }

        $candidateNames = @("test_$stem*", "${stem}.test.*", "${stem}.spec.*")
        foreach ($candidateName in $candidateNames) {
            if (-not (Test-Path -LiteralPath $testRootPath)) {
                continue
            }

            Get-ChildItem -LiteralPath $testRootPath -Recurse -File -Filter $candidateName |
                ForEach-Object {
                    $relative = [System.IO.Path]::GetRelativePath($Root, $_.FullName).Replace('/', '\')
                    [void]$targets.Add($relative)
                }
        }
    }

    return @($targets | Sort-Object)
}

function Get-RuffTargets {
    param(
        [Parameter(Mandatory)]
        [string[]]$Paths,

        [string[]]$TestTargets
    )

    $targets = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $Paths) {
        if ($path -match '^backend/.+\.py$') {
            [void]$targets.Add(($path -replace '^backend/', '').Replace('/', '\'))
        }
    }
    foreach ($target in $TestTargets) {
        [void]$targets.Add($target)
    }

    return @($targets | Sort-Object)
}

function New-BackendJobScript {
    return {
        param([string]$ConfigJson)

        $ErrorActionPreference = 'Stop'
        $config = $ConfigJson | ConvertFrom-Json
        $output = [System.Collections.Generic.List[string]]::new()

        function Invoke-Step {
            param(
                [Parameter(Mandatory)]
                [string]$Label,

                [Parameter(Mandatory)]
                [string]$FilePath,

                [Parameter(Mandatory)]
                [string[]]$Arguments
            )

            $lines = @(& $FilePath @Arguments 2>&1)
            foreach ($line in $lines) {
                $output.Add([string]$line)
            }

            $exitCode = $LASTEXITCODE
            if ($null -eq $exitCode) {
                $exitCode = 0
            }
            if ($exitCode -ne 0) {
                throw "$Label 失敗，exit code: $exitCode"
            }
        }

        try {
            Set-Location -LiteralPath (Join-Path $config.RepoRoot 'backend')
            if ($config.InstallDependencies -eq $true -or -not (Test-Path -LiteralPath '.venv')) {
                Invoke-Step -Label 'Backend dependency sync' -FilePath 'uv' -Arguments @('sync', '--frozen', '--extra', 'dev')
            }

            if ($config.ValidationMode -eq 'Live') {
                Invoke-Step -Label 'Backend integration tests' -FilePath 'uv' -Arguments @('run', 'pytest', '-q', '-m', 'integration')
            }
            else {
                $ruffTargets = @($config.BackendRuffTargets | Where-Object { $_ })
                if ($ruffTargets.Count -gt 0) {
                    $ruffArguments = @('run', 'ruff', 'check') + $ruffTargets
                    Invoke-Step -Label 'Backend Ruff' -FilePath 'uv' -Arguments $ruffArguments
                }
                else {
                    $output.Add('[CI] Backend: no changed Python files selected for Ruff')
                }
                if ($config.ValidationMode -eq 'Full') {
                    Invoke-Step -Label 'Backend full tests' -FilePath 'uv' -Arguments @('run', 'pytest', '-q', '-m', 'not integration')
                }
                else {
                    $targets = @($config.BackendTests | Where-Object { $_ })
                    if ($targets.Count -gt 0) {
                        $pytestArguments = @('run', 'pytest', '-q') + $targets + @('-m', 'not integration')
                        Invoke-Step -Label 'Backend targeted tests' -FilePath 'uv' -Arguments $pytestArguments
                    }
                    else {
                        $output.Add('[CI] Backend: no reliable targeted tests selected')
                    }
                }
            }

            [pscustomobject]@{ ExitCode = 0; Output = @($output) }
        }
        catch {
            $output.Add([string]$_)
            [pscustomobject]@{ ExitCode = 1; Output = @($output) }
        }
    }
}

function New-FrontendJobScript {
    return {
        param([string]$ConfigJson)

        $ErrorActionPreference = 'Stop'
        $config = $ConfigJson | ConvertFrom-Json
        $output = [System.Collections.Generic.List[string]]::new()

        function Invoke-Step {
            param(
                [Parameter(Mandatory)]
                [string]$Label,

                [Parameter(Mandatory)]
                [string]$FilePath,

                [Parameter(Mandatory)]
                [string[]]$Arguments
            )

            $lines = @(& $FilePath @Arguments 2>&1)
            foreach ($line in $lines) {
                $output.Add([string]$line)
            }

            $exitCode = $LASTEXITCODE
            if ($null -eq $exitCode) {
                $exitCode = 0
            }
            if ($exitCode -ne 0) {
                throw "$Label 失敗，exit code: $exitCode"
            }
        }

        try {
            Set-Location -LiteralPath (Join-Path $config.RepoRoot 'frontend')
            if ($config.InstallDependencies -eq $true -or -not (Test-Path -LiteralPath 'node_modules')) {
                Invoke-Step -Label 'Frontend dependency install' -FilePath 'pnpm' -Arguments @('install', '--frozen-lockfile')
            }

            if ($config.ValidationMode -eq 'Full') {
                Invoke-Step -Label 'Frontend tests' -FilePath 'pnpm' -Arguments @('test')
                Invoke-Step -Label 'Frontend ESLint' -FilePath 'pnpm' -Arguments @('lint')
                Invoke-Step -Label 'Frontend TypeScript and production build' -FilePath 'pnpm' -Arguments @('build')
            }
            else {
                Invoke-Step -Label 'Frontend ESLint' -FilePath 'pnpm' -Arguments @('lint')
                Invoke-Step -Label 'Frontend TypeScript check' -FilePath 'pnpm' -Arguments @('exec', 'tsc', '-b')
                $targets = @($config.FrontendTests | Where-Object { $_ })
                if ($targets.Count -gt 0) {
                    $testArguments = @('exec', 'vitest', 'run') + $targets
                    Invoke-Step -Label 'Frontend targeted tests' -FilePath 'pnpm' -Arguments $testArguments
                }
                else {
                    $output.Add('[CI] Frontend: no reliable targeted tests selected')
                }
            }

            [pscustomobject]@{ ExitCode = 0; Output = @($output) }
        }
        catch {
            $output.Add([string]$_)
            [pscustomobject]@{ ExitCode = 1; Output = @($output) }
        }
    }
}

function Invoke-CIJobs {
    param(
        [Parameter(Mandatory)]
        [bool]$RunBackend,

        [Parameter(Mandatory)]
        [bool]$RunFrontend,

        [Parameter(Mandatory)]
        [string]$ValidationMode,

        [string[]]$BackendTests,

        [string[]]$FrontendTests,

        [string[]]$BackendRuffTargets,

        [Parameter(Mandatory)]
        [bool]$InstallDependencies
    )

    $config = @{
        RepoRoot            = $repoRoot
        ValidationMode      = $ValidationMode
        InstallDependencies = $InstallDependencies
        BackendTests        = @($BackendTests)
        FrontendTests       = @($FrontendTests)
        BackendRuffTargets  = @($BackendRuffTargets)
    } | ConvertTo-Json -Compress

    $entries = [System.Collections.Generic.List[object]]::new()
    if ($RunBackend) {
        $entries.Add([pscustomobject]@{
                Name      = 'Backend'
                StartedAt = Get-Date
                Job       = Start-Job -ScriptBlock (New-BackendJobScript) -ArgumentList $config
            })
    }
    if ($RunFrontend) {
        $entries.Add([pscustomobject]@{
                Name      = 'Frontend'
                StartedAt = Get-Date
                Job       = Start-Job -ScriptBlock (New-FrontendJobScript) -ArgumentList $config
            })
    }

    $failed = $false
    foreach ($entry in $entries) {
        Wait-Job -Job $entry.Job | Out-Null
        $records = @(Receive-Job -Job $entry.Job -ErrorAction SilentlyContinue)
        $jobState = $entry.Job.State
        Remove-Job -Job $entry.Job -Force
        $record = $records | Where-Object { $_.PSObject.Properties.Name -contains 'ExitCode' } | Select-Object -Last 1
        $elapsed = [math]::Round(((Get-Date) - $entry.StartedAt).TotalSeconds, 1)

        if ($null -eq $record -or [int]$record.ExitCode -ne 0) {
            $failed = $true
            Write-Host ("[CI] {0}: FAIL ({1:n1}s)" -f $entry.Name, $elapsed) -ForegroundColor Red
            if ($record -and $record.Output) {
                $record.Output | ForEach-Object { Write-Host $_ }
            }
            elseif ($jobState -eq 'Failed') {
                Write-Host ("[CI] {0} job ended in state Failed" -f $entry.Name)
            }
        }
        else {
            Write-Host ("[CI] {0}: PASS ({1:n1}s)" -f $entry.Name, $elapsed) -ForegroundColor Green
        }
    }

    if ($failed) {
        return 1
    }

    return 0
}

$changedPaths = Get-ChangedPaths
$classification = Get-ChangeClassification -Paths $changedPaths
$backendTests = Get-TestTargets -Paths $changedPaths -Root $repoRoot -TestRoot 'backend/tests' -SourcePrefix 'backend' -TestPathPrefix 'backend/tests/'
$frontendTests = Get-TestTargets -Paths $changedPaths -Root $repoRoot -TestRoot 'frontend/src' -SourcePrefix 'frontend' -TestPathPrefix 'frontend/src/'
$backendRuffTargets = Get-RuffTargets -Paths $changedPaths -TestTargets $backendTests

$validationMode = $Mode
$runBackend = $false
$runFrontend = $false

switch ($Mode) {
    'Fast' {
        $validationMode = 'Fast'
        $runBackend = $true
        $runFrontend = $true
    }
    'Auto' {
        if ($classification.RequiresFull) {
            $validationMode = 'Full'
            $runBackend = $true
            $runFrontend = $true
        }
        elseif ($classification.Backend -or $classification.Frontend) {
            $validationMode = 'Fast'
            $runBackend = $classification.Backend
            $runFrontend = $classification.Frontend
        }
        else {
            $validationMode = 'Fast'
            $runBackend = $true
            $runFrontend = $true
        }
    }
    'Full' {
        $validationMode = 'Full'
        $runBackend = $true
        $runFrontend = $true
    }
    'Live' {
        $validationMode = 'Live'
        $runBackend = $true
    }
}

$areas = [System.Collections.Generic.List[string]]::new()
if ($classification.Backend) { $areas.Add('backend') }
if ($classification.Frontend) { $areas.Add('frontend') }
if ($classification.RequiresFull) { $areas.Add('full-escalation') }
if ($areas.Count -eq 0) { $areas.Add('none') }

Write-Host "[CI] Mode: $Mode"
Write-Host "[CI] Changed areas: $($areas -join ', ')"
if ($Mode -eq 'Auto') {
    Write-Host "[CI] Selected validation: $validationMode"
}
if ($changedPaths.Count -gt 0) {
    if ($PlanOnly) {
        Write-Host "[CI] Changed files: $($changedPaths -join ', ')"
    }
    if ($backendTests.Count -gt 0) {
        Write-Host "[CI] Backend targeted tests: $($backendTests -join ', ')"
    }
    if ($frontendTests.Count -gt 0) {
        Write-Host "[CI] Frontend targeted tests: $($frontendTests -join ', ')"
    }
}

if ($PlanOnly) {
    Write-Host '[CI] Plan only: no validation commands executed.'
    exit 0
}

$dependencyChanged = @(
    $changedPaths | Where-Object {
        $_ -match '(^|/)(pyproject\.toml|uv\.lock|requirements[^/]*|package\.json|pnpm-lock\.yaml|yarn\.lock|package-lock\.json)$'
    }
).Count -gt 0
$installDependencies = $dependencyChanged
$exitCode = Invoke-CIJobs `
    -RunBackend $runBackend `
    -RunFrontend $runFrontend `
    -ValidationMode $validationMode `
    -BackendTests $backendTests `
    -FrontendTests $frontendTests `
    -BackendRuffTargets $backendRuffTargets `
    -InstallDependencies $installDependencies

if ($exitCode -ne 0) {
    Write-Host '[CI] Result: FAIL' -ForegroundColor Red
    exit $exitCode
}

Write-Host '[CI] Result: PASS' -ForegroundColor Green
exit 0
