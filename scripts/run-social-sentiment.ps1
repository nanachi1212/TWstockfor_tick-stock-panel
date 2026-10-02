param(
    [int]$Hours = 24,
    [int]$Pages = 3,
    [ValidateSet('Auto', 'PreOpen', 'AfterClose', 'Manual', 'MissedSchedule')]
    [string]$Trigger = 'Auto',
    [switch]$NoAi
)

$ErrorActionPreference = 'Stop'
$BackendRoot = Join-Path $PSScriptRoot '..\backend'
Push-Location $BackendRoot
try {
    $arguments = @('-m', 'app.jobs.social_sentiment', '--hours', $Hours, '--pages', $Pages)
    $triggerMap = @{
        PreOpen = 'pre_open'
        AfterClose = 'after_close'
        Manual = 'manual'
        MissedSchedule = 'missed_schedule'
    }
    if ($Trigger -ne 'Auto') { $arguments += @('--trigger', $triggerMap[$Trigger]) }
    if ($NoAi) { $arguments += '--no-ai' }
    & uv run python @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
