[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateRange(1, 2147483647)]
    [int]$Pr,

    [switch]$Force,

    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot
try {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw 'GitHub CLI (gh) is required.'
    }

    $repoRaw = & gh repo view --json nameWithOwner --jq '.nameWithOwner'
    if ($LASTEXITCODE -ne 0 -or -not $repoRaw) {
        throw 'Unable to resolve the GitHub repository. Verify gh authentication.'
    }
    $repo = ([string]$repoRaw).Trim()

    $commentsJson = & gh api "repos/$repo/issues/$Pr/comments" --paginate
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read comments for PR #$Pr."
    }

    $comments = @()
    if ($commentsJson) {
        $parsed = $commentsJson | ConvertFrom-Json
        if ($null -ne $parsed) {
            $comments = @($parsed)
        }
    }

    $alreadyRequested = @(
        $comments | Where-Object {
            $body = [string]$_.body
            $body -match '(?im)^\s*@codex\s+review\s*$' -or
            $body -match 'review-policy:codex-once'
        }
    ).Count -gt 0

    if ($alreadyRequested -and -not $Force) {
        Write-Output "SKIP: Codex review was already requested for PR #$Pr."
        Write-Output 'Use -Force only when an explicit exceptional re-review is justified.'
        exit 0
    }

    $body = "@codex review" + [Environment]::NewLine + [Environment]::NewLine + '<!-- review-policy:codex-once -->'

    if ($PlanOnly) {
        Write-Output "PLAN: request one Codex review for $repo PR #$Pr."
        if ($Force) {
            Write-Output 'PLAN: -Force permits an exceptional re-review.'
        }
        exit 0
    }

    & gh pr comment $Pr --body $body
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to request Codex review for PR #$Pr."
    }

    Write-Output "REQUESTED: one Codex review for PR #$Pr."
}
finally {
    Pop-Location
}
