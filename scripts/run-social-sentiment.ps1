param(
    [int]$Hours = 24,
    [int]$Pages = 3,
    [switch]$NoAi
)

$ErrorActionPreference = 'Stop'
$BackendRoot = Join-Path $PSScriptRoot '..\backend'
Push-Location $BackendRoot
try {
    $arguments = @('-m', 'app.jobs.social_sentiment', '--hours', $Hours, '--pages', $Pages)
    if ($NoAi) { $arguments += '--no-ai' }
    & uv run python @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
