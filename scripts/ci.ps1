[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Fast', 'Full', 'Live')]
    [string]$Mode = 'Auto',

    # Optional deterministic override for CI-script checks. When omitted, Auto
    # reads unstaged, staged, untracked, and origin/main...HEAD changes.
    [string[]]$ChangedPath,

    # Print the selected plan without executing validation commands.
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot 'backend'
$FrontendRoot = Join-Path $RepoRoot 'frontend'
$ScriptPath = $MyInvocation.MyCommand.Path
$CiRunRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tick-stock-panel-ci-" + [guid]::NewGuid().ToString('N'))
$CiCache = Join-Path $CiRunRoot 'uv-cache'
$CiTemp = Join-Path $CiRunRoot 'pytest-temp'
New-Item -ItemType Directory -Force -Path $CiCache, $CiTemp | Out-Null
$CiProcessDirectory = Join-Path $CiTemp ("ci-processes-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $CiProcessDirectory | Out-Null
$env:UV_CACHE_DIR = $CiCache
$env:TEMP = $CiTemp
$env:TMP = $CiTemp
$env:TMPDIR = $CiTemp
$TrivyExcludedDirs = @(
    '**/.git',
    '**/.pytest-*',
    '**/.uv*',
    '**/.mypy_cache',
    '**/.ruff_cache',
    '**/.pytest_cache',
    '**/build',
    '**/dist',
    'data',
    'exports',
    'gui-test-screenshots',
    'release-assets',
    '**/node_modules',
    '**/.venv'
)
$TrivyExcludedDirs = @($TrivyExcludedDirs | Sort-Object -Unique)

function Normalize-RepoPath {
    param([Parameter(Mandatory)][string]$Path)

    return (($Path -replace '\\', '/') -replace '^\./', '').Trim()
}

function Get-GitPathLines {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $lines = @(& git -C $RepoRoot @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    return @(
        $lines |
            ForEach-Object { Normalize-RepoPath -Path ([string]$_) } |
            Where-Object { $_ }
    )
}

function Get-ChangedPaths {
    if ($null -ne $ChangedPath -and $ChangedPath.Count -gt 0) {
        return @(
            $ChangedPath |
                ForEach-Object { Normalize-RepoPath -Path $_ } |
                Where-Object { $_ } |
                Sort-Object -Unique
        )
    }

    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($argumentSet in @(
        @('diff', '--name-only', '--diff-filter=ACDMRTUXB'),
        @('diff', '--cached', '--name-only', '--diff-filter=ACDMRTUXB'),
        @('ls-files', '--others', '--exclude-standard')
    )) {
        foreach ($path in (Get-GitPathLines -Arguments $argumentSet)) {
            if (-not $paths.Contains($path)) {
                $paths.Add($path)
            }
        }
    }

    $originMain = @(& git -C $RepoRoot rev-parse --verify origin/main 2>$null)
    if ($LASTEXITCODE -eq 0) {
        foreach ($path in (Get-GitPathLines -Arguments @('diff', '--name-only', 'origin/main...HEAD'))) {
            if (-not $paths.Contains($path)) {
                $paths.Add($path)
            }
        }
    }
    else {
        Write-Warning '找不到 origin/main，已略過 origin/main...HEAD 變更。'
    }

    return @($paths | Sort-Object)
}

function Test-AnyPath {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths,
        [Parameter(Mandatory)][string]$Pattern
    )

    return [bool]($Paths | Where-Object { $_ -match $Pattern } | Select-Object -First 1)
}

function Get-ChangeProfile {
    param([Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths)

    $backend = Test-AnyPath -Paths $Paths -Pattern '^(backend/(app|tests|scripts)/.*\.(py|pyi)|backend/pyproject\.toml|backend/uv\.lock)$'
    $frontend = Test-AnyPath -Paths $Paths -Pattern '^frontend/'
    $dependency = Test-AnyPath -Paths $Paths -Pattern '(^|/)(uv\.lock|pyproject\.toml|requirements[^/]*\.txt|poetry\.lock|package\.json|pnpm-lock\.yaml|package-lock\.json|yarn\.lock|bun\.lockb)$'
    $schemaOrMigration = Test-AnyPath -Paths $Paths -Pattern '(^|/)(schema|schemas|migrations?|contracts?)(/|\.|$)|(^|/).*\.(sql|graphql|gql)$'
    $sharedContract = Test-AnyPath -Paths $Paths -Pattern '^(backend/app/api/|backend/app/(core|tickflow|data_providers)/|frontend/src/(lib/api\.|lib/queryKeys\.|types/|extensions/)|shared/|contracts/)'
    $scriptOrWorkflow = Test-AnyPath -Paths $Paths -Pattern '^(scripts/|\.github/workflows/)'
    $documentation = Test-AnyPath -Paths $Paths -Pattern '(^|/)(README|CONTRIBUTING|AGENTS)(\.|$)|^(docs|specs)/|\.(md|mdx|rst|txt)$'

    $highRisk = $dependency -or $schemaOrMigration -or $sharedContract
    $code = $backend -or $frontend

    return [pscustomobject]@{
        Backend = $backend
        Frontend = $frontend
        Dependency = $dependency
        SchemaOrMigration = $schemaOrMigration
        SharedContract = $sharedContract
        ScriptOrWorkflow = $scriptOrWorkflow
        Documentation = $documentation
        HighRisk = $highRisk
        Code = $code
    }
}

function Get-RelativeRepoPath {
    param([Parameter(Mandatory)][string]$FullPath)

    return (Resolve-Path -LiteralPath $FullPath).Path.Substring($RepoRoot.Length + 1) -replace '\\', '/'
}

function Add-PathIfExists {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Target,
        [Parameter(Mandatory)][string]$Path
    )

    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and -not $Target.Contains($Path)) {
        $Target.Add($Path)
    }
}

function Get-BackendTestFiles {
    param([Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths)

    $testRoot = Join-Path $BackendRoot 'tests'
    $allTests = @(Get-ChildItem -LiteralPath $testRoot -Recurse -File -Filter 'test_*.py')
    $selected = [System.Collections.Generic.List[string]]::new()
    $fallbackRequired = $false

    foreach ($path in $Paths | Where-Object { $_ -match '^backend/' }) {
        if ($path -match '^backend/tests/(.+\.py)$') {
            $candidate = Join-Path $BackendRoot ($Matches[1] -replace '/', '\\')
            Add-PathIfExists -Target $selected -Path $candidate
            continue
        }

        if ($path -notmatch '^backend/app/.+\.pyi?$') {
            continue
        }

        $leaf = [IO.Path]::GetFileNameWithoutExtension($path)
        $directoryParts = @((($path -split '/') | Where-Object { $_ -and $_ -notin @('backend', 'app', 'api') }))
        $domain = if ($directoryParts.Count -gt 1) { $directoryParts[$directoryParts.Count - 2] } else { '' }
        $baseNames = [System.Collections.Generic.List[string]]::new()
        $baseNames.Add($leaf)
        if ($leaf.EndsWith('s')) { $baseNames.Add($leaf.Substring(0, $leaf.Length - 1)) }
        foreach ($suffix in @('_service', '_store', '_models', '_model', '_client', '_manager', '_engine', '_provider', '_repository', '_routes', '_api', '_job', '_jobs', '_scheduler', '_runner', '_config', '_settings')) {
            if ($leaf.EndsWith($suffix)) {
                $baseNames.Add($leaf.Substring(0, $leaf.Length - $suffix.Length))
            }
        }

        $matches = @($allTests | Where-Object {
            $testName = $_.BaseName.Substring(5)
            ($baseNames | Where-Object { $testName -eq $_ -or $testName -eq "$domain`_$($_)" })
        })
        foreach ($match in $matches) {
            Add-PathIfExists -Target $selected -Path $match.FullName
        }

        if ($matches.Count -eq 0) {
            $significantTokens = @($leaf -split '_' | Where-Object { $_.Length -ge 4 -and $_ -notin @('data', 'main', 'test') })
            $tokenMatches = @($allTests | Where-Object {
                $testName = $_.BaseName.ToLowerInvariant()
                [bool]($significantTokens | Where-Object { $testName.Contains($_.ToLowerInvariant()) })
            })
            foreach ($match in $tokenMatches) {
                Add-PathIfExists -Target $selected -Path $match.FullName
            }
        }

        if ($matches.Count -eq 0 -and $selected.Count -eq 0) {
            $fallbackRequired = $true
        }
    }

    if ($fallbackRequired) {
        return [pscustomobject]@{ Files = @(); Fallback = $true }
    }

    return [pscustomobject]@{ Files = @($selected | Sort-Object); Fallback = $false }
}

function Get-FrontendTestFiles {
    param([Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths)

    $srcRoot = Join-Path $FrontendRoot 'src'
    $allTests = @(Get-ChildItem -LiteralPath $srcRoot -Recurse -File | Where-Object { $_.Name -match '\.(test|spec)\.[cm]?[jt]sx?$' })
    $selected = [System.Collections.Generic.List[string]]::new()
    $fallbackRequired = $false

    foreach ($path in $Paths | Where-Object { $_ -match '^frontend/' }) {
        if ($path -match '^frontend/src/.+\.(test|spec)\.[cm]?[jt]sx?$') {
            $candidate = Join-Path $RepoRoot ($path -replace '/', '\\')
            Add-PathIfExists -Target $selected -Path $candidate
            continue
        }

        if ($path -notmatch '^frontend/src/.+\.[cm]?[jt]sx?$') {
            continue
        }

        $leaf = [IO.Path]::GetFileNameWithoutExtension($path)
        $matches = @($allTests | Where-Object {
            $_.BaseName -match "^$([regex]::Escape($leaf))\.(test|spec)$" -or
            $_.BaseName -match "$([regex]::Escape($leaf))\.(test|spec)$"
        })
        foreach ($match in $matches) {
            Add-PathIfExists -Target $selected -Path $match.FullName
        }
        if ($matches.Count -eq 0) {
            $fallbackRequired = $true
        }
    }

    if ($fallbackRequired) {
        return [pscustomobject]@{ Files = @(); Fallback = $true }
    }

    return [pscustomobject]@{ Files = @($selected | Sort-Object); Fallback = $false }
}

function Get-FrontendBuildNeeded {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths,
        [Parameter(Mandatory)][bool]$Full
    )

    if ($Full) { return $true }

    return Test-AnyPath -Paths $Paths -Pattern '^frontend/(package\.json|pnpm-lock\.yaml|tsconfig[^/]*\.json|vite\.config\.|postcss\.config\.|tailwind\.config\.|src/(main\.|router\.|lib/|pages/|extensions/|.*\.(css|scss)))'
}

function Add-Lane {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lanes,
        [Parameter(Mandatory)][string]$Lane
    )

    if (-not $Lanes.Contains($Lane)) { $Lanes.Add($Lane) }
}

function Get-Lanes {
    param(
        [Parameter(Mandatory)][string]$SelectedMode,
        [Parameter(Mandatory)]$Profile,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths
    )

    $lanes = [System.Collections.Generic.List[string]]::new()
    if ($SelectedMode -eq 'Live') {
        Add-Lane -Lanes $lanes -Lane 'backend-live-integration'
        return @($lanes)
    }

    if ($SelectedMode -eq 'Auto' -and $Profile.Dependency) {
        Add-Lane -Lanes $lanes -Lane 'trivy-dependency'
    }

    if ($SelectedMode -eq 'Full' -or ($SelectedMode -eq 'Auto' -and $Profile.HighRisk)) {
        Add-Lane -Lanes $lanes -Lane 'backend-full'
        Add-Lane -Lanes $lanes -Lane 'frontend-full'
        return @($lanes)
    }

    $hasScriptChange = $Profile.ScriptOrWorkflow
    if ($Profile.Backend) { Add-Lane -Lanes $lanes -Lane 'backend-fast' }
    if ($Profile.Frontend) { Add-Lane -Lanes $lanes -Lane 'frontend-fast' }

    if ($lanes.Count -eq 0 -and $hasScriptChange) {
        Add-Lane -Lanes $lanes -Lane 'ci-script-check'
    }
    if ($lanes.Count -eq 0 -and -not $Profile.Documentation) {
        Add-Lane -Lanes $lanes -Lane 'no-code-change'
    }

    return @($lanes)
}

function Invoke-ExternalStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $started = Get-Date
    try {
        Push-Location -LiteralPath $WorkingDirectory
        $output = @(& $FilePath @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    finally {
        Pop-Location
    }

    return [pscustomobject]@{
        Name = $Name
        Passed = ($exitCode -eq 0)
        ExitCode = $exitCode
        Duration = ((Get-Date) - $started).TotalSeconds
        Output = ($output | Out-String).Trim()
    }
}

function Stop-CiProcessTree {
    param([Parameter(Mandatory)][string]$ProcessDirectory)

    if (-not (Test-Path -LiteralPath $ProcessDirectory -PathType Container)) { return }

    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $childrenByParent = @{}
    foreach ($process in $processes) {
        $parentId = [string]$process.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = [System.Collections.Generic.List[object]]::new()
        }
        $childrenByParent[$parentId].Add($process)
    }

    $targets = [System.Collections.Generic.List[object]]::new()
    foreach ($metadataPath in @(Get-ChildItem -LiteralPath $ProcessDirectory -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $metadata = Get-Content -LiteralPath $metadataPath.FullName -Raw | ConvertFrom-Json
            $queue = [System.Collections.Generic.Queue[object]]::new()
            $root = $processes | Where-Object { $_.ProcessId -eq [int]$metadata.ProcessId } | Select-Object -First 1
            if ($null -eq $root) {
                $root = [pscustomobject]@{ ProcessId = [int]$metadata.ProcessId }
            }
            $queue.Enqueue([pscustomobject]@{ Process = $root; Depth = 0 })
            while ($queue.Count -gt 0) {
                $entry = $queue.Dequeue()
                $targets.Add($entry)
                foreach ($child in @($childrenByParent[[string]$entry.Process.ProcessId])) {
                    $queue.Enqueue([pscustomobject]@{ Process = $child; Depth = $entry.Depth + 1 })
                }
            }
        }
        catch {
            Write-Warning "無法讀取 CI process metadata '$($metadataPath.Name)'：$($_.Exception.Message)"
        }
    }

    foreach ($target in @($targets | Sort-Object -Property @(
        @{ Expression = { $_.Depth }; Descending = $true },
        @{ Expression = { $_.Process.ProcessId }; Descending = $false }
    ))) {
        try {
            $running = Get-Process -Id $target.Process.ProcessId -ErrorAction SilentlyContinue
            if ($null -ne $running) {
                Stop-Process -Id $running.Id -Force -ErrorAction Stop
                Write-Output "已清理本次 CI process tree PID $($running.Id)"
            }
        }
        catch {
            Write-Warning "無法清理 CI process PID $($target.Process.ProcessId)：$($_.Exception.Message)"
        }
    }
}

function Start-DomainValidationJob {
    param(
        [Parameter(Mandatory)][ValidateSet('backend', 'frontend')][string]$Domain,
        [Parameter(Mandatory)][ValidateSet('Fast', 'Full')][string]$ValidationMode,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Paths,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$BackendTests,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$FrontendTests,
        [Parameter(Mandatory)][bool]$FrontendBuild,
        [Parameter(Mandatory)][string]$ProcessDirectory
    )

    $jobScript = {
        param($Root, $DomainName, $RunMode, $Changed, $BackendTestList, $FrontendTestList, $BuildFrontend, $RunProcessDirectory)

        Set-StrictMode -Version Latest
        $ErrorActionPreference = 'Stop'
        $backend = Join-Path $Root 'backend'
        $frontend = Join-Path $Root 'frontend'

        function Emit-ProcessOutput {
            param(
                [Parameter(Mandatory)][string]$Name,
                [Parameter(Mandatory)][string]$Stream,
                [Parameter(Mandatory)][string]$Path,
                [Parameter(Mandatory)][ref]$Offset,
                [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines
            )

            if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
            $fileHandle = $null
            $textReader = $null
            try {
                $fileHandle = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                if ($fileHandle.Length -le $Offset.Value) { return }
                $fileHandle.Position = $Offset.Value
                $textReader = [IO.StreamReader]::new($fileHandle, [Text.Encoding]::UTF8, $true)
                $delta = $textReader.ReadToEnd()
                $Offset.Value = $fileHandle.Position
            }
            finally {
                if ($null -ne $textReader) { $textReader.Dispose() }
                elseif ($null -ne $fileHandle) { $fileHandle.Dispose() }
            }
            foreach ($line in @($delta -split "`r?`n")) {
                if (-not $line) { continue }
                $Lines.Add($line)
                Write-Output ([pscustomobject]@{
                    CiLiveOutput = $true
                    Name = $Name
                    Stream = $Stream
                    Line = $line
                })
            }
        }

        function Run-Step {
            param([string]$Name, [string]$Directory, [string]$Command, [string[]]$Arguments, [System.Collections.Generic.List[object]]$Results)
            $started = Get-Date
            $runId = [guid]::NewGuid().ToString('N')
            $stdoutPath = Join-Path $RunProcessDirectory "$runId.stdout.log"
            $stderrPath = Join-Path $RunProcessDirectory "$runId.stderr.log"
            $metadataPath = Join-Path $RunProcessDirectory "$runId.json"
            $outputLines = [System.Collections.Generic.List[string]]::new()
            $process = $null
            $stdoutOffset = 0
            $stderrOffset = 0
            try {
                $startArguments = @($Arguments | ForEach-Object {
                    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\\"') + '"' } else { $_ }
                })
                $process = Start-Process -FilePath $Command -ArgumentList $startArguments -WorkingDirectory $Directory -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden
                Set-Content -LiteralPath $metadataPath -Value (@{
                    ProcessId = $process.Id
                    Name = $Name
                    Command = $Command
                    StartedUtc = [DateTime]::UtcNow.ToString('O')
                } | ConvertTo-Json) -Encoding utf8
                while (-not $process.HasExited) {
                    Emit-ProcessOutput -Name $Name -Stream 'stdout' -Path $stdoutPath -Offset ([ref]$stdoutOffset) -Lines $outputLines
                    Emit-ProcessOutput -Name $Name -Stream 'stderr' -Path $stderrPath -Offset ([ref]$stderrOffset) -Lines $outputLines
                    Start-Sleep -Milliseconds 100
                }
                $process.WaitForExit()
                Emit-ProcessOutput -Name $Name -Stream 'stdout' -Path $stdoutPath -Offset ([ref]$stdoutOffset) -Lines $outputLines
                Emit-ProcessOutput -Name $Name -Stream 'stderr' -Path $stderrPath -Offset ([ref]$stderrOffset) -Lines $outputLines
                $exitCode = $process.ExitCode
            }
            catch {
                $outputLines.Add($_.Exception.Message)
                Write-Output ([pscustomobject]@{ CiLiveOutput = $true; Name = $Name; Stream = 'runner'; Line = $_.Exception.Message })
                $exitCode = 1
            }
            finally {
                if ($null -ne $process) { $process.Dispose() }
                Remove-Item -LiteralPath $metadataPath, $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
            }
            $Results.Add([pscustomobject]@{
                Name = $Name
                Passed = ($exitCode -eq 0)
                ExitCode = $exitCode
                Duration = ((Get-Date) - $started).TotalSeconds
                Output = ($outputLines -join [Environment]::NewLine).Trim()
            }) | Out-Null
        }

        $results = [System.Collections.Generic.List[object]]::new()
        if ($DomainName -eq 'backend') {
            $ruffTargets = @()
            if ($RunMode -eq 'Full') {
                $ruffTargets = @('app', 'tests')
            }
            else {
                $ruffTargets = @($Changed | Where-Object { $_ -match '^backend/.+\.pyi?$' } | ForEach-Object { $_.Substring(8) })
                if ($ruffTargets.Count -eq 0) { $ruffTargets = @('app') }
            }
            if ($RunMode -ne 'Full') {
                # ext_data.py predates the current RUF/SIM/B policy and still has
                # unrelated baseline findings. Keep basic correctness linting for
                # that touched legacy file without weakening other targets.
                $legacyRuffTargets = @($ruffTargets | Where-Object { $_ -eq 'app/services/ext_data.py' })
                $standardRuffTargets = @($ruffTargets | Where-Object { $_ -ne 'app/services/ext_data.py' })
                if ($standardRuffTargets.Count -gt 0) {
                    Run-Step -Name 'Backend Ruff' -Directory $backend -Command 'uv' -Arguments (@('run', '--frozen', '--extra', 'dev', 'ruff', 'check') + $standardRuffTargets) -Results $results
                }
                if ($legacyRuffTargets.Count -gt 0) {
                    $legacyArgs = @('run', '--frozen', '--extra', 'dev', 'ruff', 'check', '--select', 'E,F,I,N,UP', '--ignore', 'E501') + $legacyRuffTargets
                    Run-Step -Name 'Backend Ruff (legacy baseline scope)' -Directory $backend -Command 'uv' -Arguments $legacyArgs -Results $results
                }
            }

            $pytestArgs = @('run', '--frozen', '--extra', 'dev', 'pytest', '-q')
            if ($BackendTestList.Count -gt 0) {
                $pytestArgs += @($BackendTestList | ForEach-Object { $_.Substring($backend.Length + 1) })
            }
            $pytestArgs += @('-m', 'not integration')
            $testName = if ($BackendTestList.Count -gt 0) { 'Backend related pytest' } else { 'Backend pytest fallback (not integration)' }
            Run-Step -Name $testName -Directory $backend -Command 'uv' -Arguments $pytestArgs -Results $results
        }
        else {
            $eslintTargets = @($Changed | Where-Object { $_ -match '^frontend/src/.+\.(c|m)?[jt]sx?$' } | ForEach-Object { $_.Substring(9) })
            if ($RunMode -eq 'Full' -or $eslintTargets.Count -eq 0) {
                $eslintTargets = @('.')
            }

            $eslintCommand = Join-Path $frontend 'node_modules\.bin\eslint.cmd'
            $eslintArguments = $eslintTargets
            if (-not (Test-Path -LiteralPath $eslintCommand -PathType Leaf)) {
                $eslintCommand = 'pnpm'
                $eslintArguments = @('exec', 'eslint') + $eslintTargets
            }
            Run-Step -Name 'Frontend ESLint' -Directory $frontend -Command $eslintCommand -Arguments $eslintArguments -Results $results

            $tscCommand = Join-Path $frontend 'node_modules\.bin\tsc.cmd'
            $tscArguments = @('--noEmit', '--pretty', 'false')
            if (-not (Test-Path -LiteralPath $tscCommand -PathType Leaf)) {
                $tscCommand = 'pnpm'
                $tscArguments = @('exec', 'tsc') + $tscArguments
            }
            Run-Step -Name 'Frontend TypeScript' -Directory $frontend -Command $tscCommand -Arguments $tscArguments -Results $results

            $vitestCommand = Join-Path $frontend 'node_modules\.bin\vitest.cmd'
            $vitestArgs = @('run')
            if ($FrontendTestList.Count -gt 0) {
                $vitestArgs += @($FrontendTestList | ForEach-Object { $_.Substring($frontend.Length + 1) })
                $testName = 'Frontend related tests'
            }
            else {
                $testName = 'Frontend tests fallback (full mock suite)'
            }
            if (-not (Test-Path -LiteralPath $vitestCommand -PathType Leaf)) {
                $vitestCommand = 'pnpm'
                $vitestArgs = @('test', '--') + $vitestArgs
            }
            Run-Step -Name $testName -Directory $frontend -Command $vitestCommand -Arguments $vitestArgs -Results $results

            if ($BuildFrontend) {
                $viteCommand = Join-Path $frontend 'node_modules\.bin\vite.cmd'
                if ((Test-Path -LiteralPath $tscCommand -PathType Leaf) -and (Test-Path -LiteralPath $viteCommand -PathType Leaf)) {
                    Run-Step -Name 'Frontend build TypeScript' -Directory $frontend -Command $tscCommand -Arguments @('-b') -Results $results
                    Run-Step -Name 'Frontend Vite build' -Directory $frontend -Command $viteCommand -Arguments @('build') -Results $results
                }
                else {
                    Run-Step -Name 'Frontend build' -Directory $frontend -Command 'pnpm' -Arguments @('build') -Results $results
                }
            }
        }
        return @($results)
    }

    return Start-Job -ScriptBlock $jobScript -ArgumentList $RepoRoot, $Domain, $ValidationMode, $Paths, $BackendTests, $FrontendTests, $FrontendBuild, $ProcessDirectory
}

function Invoke-ScriptCheck {
    param([Parameter(Mandatory)][string[]]$Paths)

    $errors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        return [pscustomobject]@{ Name = 'CI script syntax'; Passed = $false; Duration = 0; Output = ($errors | ForEach-Object { $_.Message }) -join [Environment]::NewLine }
    }

    $probeCases = @(
        @{ Path = 'backend/app/example.py'; Expected = 'backend-fast' },
        @{ Path = 'frontend/src/example.tsx'; Expected = 'frontend-fast' },
        @{ Path = 'backend/pyproject.toml'; Expected = 'backend-full' },
        @{ Path = 'README.md'; Expected = '無需執行程式驗證' }
    )
    foreach ($probe in $probeCases) {
        $probeOutput = @(& $PSHOME\pwsh.exe -NoProfile -File $ScriptPath -Mode Auto -ChangedPath $probe.Path -PlanOnly 2>&1)
        if ($LASTEXITCODE -ne 0 -or -not (($probeOutput -join [Environment]::NewLine) -match [regex]::Escape($probe.Expected))) {
            return [pscustomobject]@{
                Name = 'CI script plan probes'
                Passed = $false
                Duration = 0
                Output = "Probe '$($probe.Path)' did not select '$($probe.Expected)'.`n$($probeOutput -join [Environment]::NewLine)"
            }
        }
    }

    return [pscustomobject]@{ Name = 'CI script syntax and plan probes'; Passed = $true; Duration = 0; Output = '' }
}

function Invoke-TrivyDependencyScan {
    $trivy = Get-Command trivy -ErrorAction SilentlyContinue
    if ($null -eq $trivy) {
        $localTrivy = Join-Path $env:LOCALAPPDATA 'Programs\Trivy\trivy.exe'
        if (Test-Path -LiteralPath $localTrivy -PathType Leaf) {
            $trivyPath = $localTrivy
        }
        else {
            return [pscustomobject]@{
                Name = 'Trivy dependency scan'
                Passed = $false
                ExitCode = 1
                Duration = 0
                Output = '找不到 trivy 執行檔。請先安裝 Trivy 並加入 PATH。'
            }
        }
    }
    else {
        $trivyPath = $trivy.Source
    }

    $arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in @('fs', '.', '--severity', 'HIGH,CRITICAL', '--exit-code', '1')) {
        $arguments.Add($argument)
    }
    foreach ($directory in $TrivyExcludedDirs) {
        $arguments.Add('--skip-dirs')
        $arguments.Add($directory)
    }
    return Invoke-ExternalStep -Name 'Trivy dependency scan' -WorkingDirectory $RepoRoot -FilePath $trivyPath -Arguments $arguments.ToArray()
}

function Write-CompactFailureOutput {
    param([Parameter(Mandatory)][string]$Output)

    $lines = @($Output -split "`r?`n")
    if ($lines.Count -gt 80) {
        Write-Output "失敗輸出已截短，僅顯示最後 80 行："
        $lines = @($lines | Select-Object -Last 80)
    }
    Write-Output ($lines -join [Environment]::NewLine)
}

function Receive-CiJobItems {
    param(
        [Parameter(Mandatory)]$Job,
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Results,
        [switch]$Final
    )

    $resultCountBefore = $Results.Count
    foreach ($item in @(Receive-Job -Job $Job -ErrorAction SilentlyContinue)) {
        if ($item.PSObject.Properties.Name -contains 'CiLiveOutput') {
            Write-Output ("[$($item.Name)/$($item.Stream)] $($item.Line)")
            continue
        }
        if ($null -ne $item.PSObject.Properties['Passed']) {
            $Results.Add($item)
            if ($null -eq $Job.PSObject.Properties['CiResultSeen']) {
                $Job | Add-Member -MemberType NoteProperty -Name CiResultSeen -Value $true
            }
        }
    }
    if ($Final -and $Job.State -in @('Completed', 'Failed', 'Stopped') -and $null -eq $Job.PSObject.Properties['CiResultSeen']) {
        $child = $Job.ChildJobs | Select-Object -First 1
        $reason = if ($null -ne $child) { $child.JobStateInfo.Reason } else { $null }
        $childErrors = if ($null -ne $child) { @($child.Error | ForEach-Object { $_.ToString() }) } else { @() }
        $jobState = [string]$Job.State
        $childState = if ($null -ne $child) { [string]$child.State } else { 'no-child' }
        $message = if ($null -ne $reason) { $reason.Exception.Message } elseif (@($childErrors).Count -gt 0) { $childErrors -join [Environment]::NewLine } else { "CI validation job completed without a result (job=$jobState, child=$childState)." }
        $Results.Add([pscustomobject]@{ Name = 'CI validation job'; Passed = $false; ExitCode = 1; Duration = 0; Output = $message })
    }
}

trap {
    Stop-CiProcessTree -ProcessDirectory $CiProcessDirectory
    throw
}

$changed = @(Get-ChangedPaths)
$profile = Get-ChangeProfile -Paths $changed
$lanes = @(Get-Lanes -SelectedMode $Mode -Profile $profile -Paths $changed)
$backendTests = if ($profile.Backend) { Get-BackendTestFiles -Paths $changed } else { [pscustomobject]@{ Files = @(); Fallback = $false } }
$frontendTests = if ($profile.Frontend) { Get-FrontendTestFiles -Paths $changed } else { [pscustomobject]@{ Files = @(); Fallback = $false } }
$frontendBuild = Get-FrontendBuildNeeded -Paths $changed -Full ($Mode -eq 'Full' -or ($Mode -eq 'Auto' -and $profile.HighRisk))

Write-Output "Mode: $Mode"
Write-Output "變更檔案: $($changed.Count) 個"
if ($changed.Count -gt 0) {
    $displayPaths = @($changed | Select-Object -First 20)
    Write-Output ("變更摘要: " + ($displayPaths -join ', ') + $(if ($changed.Count -gt 20) { ' ...' } else { '' }))
}
$changeTypes = [System.Collections.Generic.List[string]]::new()
foreach ($entry in @(
    @{ Name = 'backend/Python'; Value = $profile.Backend },
    @{ Name = 'frontend/TS/TSX'; Value = $profile.Frontend },
    @{ Name = 'dependency/lockfile'; Value = $profile.Dependency },
    @{ Name = 'schema/migration'; Value = $profile.SchemaOrMigration },
    @{ Name = 'shared API/核心模組'; Value = $profile.SharedContract },
    @{ Name = 'scripts/workflow'; Value = $profile.ScriptOrWorkflow },
    @{ Name = '文件'; Value = $profile.Documentation }
)) {
    if ($entry.Value) { $changeTypes.Add($entry.Name) }
}
Write-Output ("變更類型: " + $(if ($changeTypes.Count) { $changeTypes -join ', ' } else { '未分類或無變更' }))
Write-Output ("Validation lanes: " + $(if ($lanes.Count) { $lanes -join ', ' } else { '無需執行程式驗證' }))
if ($profile.Backend -and $backendTests.Fallback) { Write-Output 'Backend tests: 找不到明確對應測試，將使用非 integration backend fallback。' }
if ($profile.Frontend -and $frontendTests.Fallback) { Write-Output 'Frontend tests: 找不到明確對應測試，將使用完整 mock suite fallback。' }
if ($profile.Frontend -and $frontendBuild -and $Mode -ne 'Full') { Write-Output 'Frontend build: 本次變更觸及入口、共享模組、頁面、設定或樣式，納入 build。' }

if ($PlanOnly) {
    Write-Output 'PlanOnly: 未執行 validation commands。'
    exit 0
}

$results = [System.Collections.Generic.List[object]]::new()
$startedAll = Get-Date
$jobs = [System.Collections.Generic.List[object]]::new()

try {
    foreach ($lane in $lanes) {
        switch ($lane) {
            'trivy-dependency' {
                $results.Add((Invoke-TrivyDependencyScan))
            }
            'backend-fast' {
                $jobs.Add((Start-DomainValidationJob -Domain backend -ValidationMode Fast -Paths $changed -BackendTests $backendTests.Files -FrontendTests @() -FrontendBuild $false -ProcessDirectory $CiProcessDirectory))
            }
            'frontend-fast' {
                $jobs.Add((Start-DomainValidationJob -Domain frontend -ValidationMode Fast -Paths $changed -BackendTests @() -FrontendTests $frontendTests.Files -FrontendBuild $frontendBuild -ProcessDirectory $CiProcessDirectory))
            }
            'backend-full' {
                $jobs.Add((Start-DomainValidationJob -Domain backend -ValidationMode Full -Paths $changed -BackendTests @() -FrontendTests @() -FrontendBuild $false -ProcessDirectory $CiProcessDirectory))
            }
            'frontend-full' {
                $jobs.Add((Start-DomainValidationJob -Domain frontend -ValidationMode Full -Paths $changed -BackendTests @() -FrontendTests @() -FrontendBuild $true -ProcessDirectory $CiProcessDirectory))
            }
            'backend-live-integration' {
                $results.Add((Invoke-ExternalStep -Name 'Backend live integration' -WorkingDirectory $BackendRoot -FilePath 'uv' -Arguments @('run', '--frozen', '--extra', 'dev', 'pytest', '-q', '-m', 'integration')))
            }
            'ci-script-check' {
                $results.Add((Invoke-ScriptCheck -Paths $changed))
            }
            'no-code-change' {
                $results.Add([pscustomobject]@{ Name = 'No code validation needed'; Passed = $true; Duration = 0; Output = '' })
            }
        }
    }

    while (@($jobs | Where-Object { $_.State -in @('NotStarted', 'Running') }).Count -gt 0) {
        foreach ($job in $jobs) {
            Receive-CiJobItems -Job $job -Results $results
        }
        Start-Sleep -Milliseconds 200
    }
    foreach ($job in $jobs) {
        Receive-CiJobItems -Job $job -Results $results -Final
        Remove-Job -Job $job -Force
    }
    if ($jobs.Count -gt 0 -and $results.Count -eq 0) {
        $results.Add([pscustomobject]@{
            Name = 'CI runner'
            Passed = $false
            ExitCode = 1
            Duration = 0
            Output = 'Validation jobs completed without returning a result.'
        })
    }
}
catch {
    $results.Add([pscustomobject]@{ Name = 'CI runner'; Passed = $false; Duration = 0; Output = $_.Exception.Message })
    foreach ($job in $jobs) {
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}
finally {
    foreach ($job in $jobs) {
        if ($job.State -in @('NotStarted', 'Running')) {
            Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
    Stop-CiProcessTree -ProcessDirectory $CiProcessDirectory
    Remove-Item -LiteralPath $CiProcessDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $CiRunRoot -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($result in $results) {
    $status = if ($result.Passed) { 'PASS' } else { 'FAIL' }
    $duration = '{0:N1}s' -f [double]$result.Duration
    Write-Output ("[$status] $($result.Name) ($duration)")
    if (-not $result.Passed -and $result.Output) {
        Write-CompactFailureOutput -Output $result.Output
    }
}

$failed = @($results | Where-Object { -not $_.Passed })
$totalDuration = '{0:N1}s' -f ((Get-Date) - $startedAll).TotalSeconds
if ($failed.Count -eq 0) {
    Write-Output "總結果: PASS ($totalDuration)"
    exit 0
}

Write-Output "總結果: FAIL，$($failed.Count) 個 lane 失敗 ($totalDuration)"
exit 1
