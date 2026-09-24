<#
.SYNOPSIS
    Nightly launcher for the Taiwan historical backfill worker (Background Data Lane).

.DESCRIPTION
    Runs one bounded pass of the A2a observed-universe census and the A2b TWSE
    point-in-time classification, then exits.

    Reaching the daily budget is a normal finish (exit 0).  If a previous run is
    still going, the worker sees the lock and exits 0 without doing work, so it
    is safe to schedule this every night unconditionally.

    Nothing it writes goes into Git: all output lands under the repo's data
    directory, which .gitignore already excludes.

.PARAMETER SessionBudget
    Census sessions per exchange per run. Each session costs 1 request per
    exchange. Default 300.

.PARAMETER ClassificationRequestBudget
    A2b classification requests per run. Each first-seen date costs 35 requests.
    Default 1200 (about 34 dates per run).

.PARAMETER LongRun
    Unlimited budgets: keep working until the backfill is done or you press
    Ctrl+C. Rate limiting, bounded retry, the single-instance lock, atomic
    writes and checkpointing are all unchanged, and the next run resumes from
    the checkpoint. Provider rpm is never raised.

.PARAMETER Status
    Print the machine-readable status snapshot and exit without doing work.

.PARAMETER RetryEmpty
    Re-query empty_unknown census partitions without promoting empty responses to holidays.

.EXAMPLE
    .\scripts\run_taiwan_historical_backfill.ps1

.EXAMPLE
    .\scripts\run_taiwan_historical_backfill.ps1 -Status

.EXAMPLE
    .\scripts\run_taiwan_historical_backfill.ps1 -SessionBudget 600 -ClassificationRequestBudget 2400
#>
[CmdletBinding()]
param(
    [int]$SessionBudget = 300,
    [int]$ClassificationRequestBudget = 1200,
    [switch]$Status,
    [switch]$LongRun,
    [switch]$ForceUnlock,
    [switch]$RetryEmpty
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$LogDir = Join-Path $RepoRoot 'data\logs'

if (-not (Test-Path $BackendDir)) {
    Write-Error "backend directory not found at $BackendDir"
    exit 1
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$LogFile = Join-Path $LogDir ("taiwan_historical_backfill_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

$uv = (Get-Command uv -ErrorAction SilentlyContinue)
if (-not $uv) {
    Write-Error "uv is not on PATH. Install uv or add it to PATH for the scheduled task's user."
    exit 1
}

$arguments = @('run', '--frozen', 'python', '-m', 'scripts.taiwan_historical_backfill')

if ($Status) {
    $arguments += '--status'
} elseif ($LongRun) {
    $arguments += '--long-run'
    if ($ForceUnlock) { $arguments += '--force-unlock' }
} else {
    $arguments += @(
        '--daily-session-budget', $SessionBudget,
        '--classification-request-budget', $ClassificationRequestBudget
    )
    if ($RetryEmpty) { $arguments += '--retry-empty' }
    if ($ForceUnlock) { $arguments += '--force-unlock' }
}

Push-Location $BackendDir
try {
    $started = Get-Date
    if ($Status) {
        & uv @arguments
        $code = $LASTEXITCODE
    } else {
        "=== run started $($started.ToString('s')) ===" | Add-Content -Path $LogFile -Encoding utf8
        & uv @arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
        $code = $LASTEXITCODE
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        "=== run finished exit=$code elapsed=${elapsed}s ===" | Add-Content -Path $LogFile -Encoding utf8
    }
    exit $code
} finally {
    Pop-Location
}
