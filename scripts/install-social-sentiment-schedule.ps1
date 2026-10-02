param(
    [string]$RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path,
    [string]$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = 'Stop'
$PreOpenTaskName = 'TickStock Social Sentiment PreOpen'
$AfterCloseTaskName = 'TickStock Social Sentiment AfterClose'
$RecoveryTaskName = 'TickStock Social Sentiment PreOpen Recovery'
$Runner = Join-Path $RepositoryRoot 'scripts\run-social-sentiment.ps1'

if (-not (Test-Path -LiteralPath $PowerShellPath -PathType Leaf)) {
    throw "PowerShell executable not found: $PowerShellPath"
}
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Social Sentiment runner not found: $Runner"
}

$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Register-SocialSentimentTask {
    param(
        [Parameter(Mandatory)] [string]$TaskName,
        [Parameter(Mandatory)] [string]$At,
        [Parameter(Mandatory)] [ValidateSet('PreOpen', 'AfterClose')] [string]$Trigger
    )

    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Runner`" -Trigger $Trigger"
    $action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments -WorkingDirectory $RepositoryRoot
    $daily = New-ScheduledTaskTrigger -Daily -At $At
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $daily -Settings $settings -Principal $principal -Description "TickStock Social Sentiment $Trigger refresh" -Force | Out-Null
}

Register-SocialSentimentTask -TaskName $PreOpenTaskName -At '08:30' -Trigger PreOpen
Register-SocialSentimentTask -TaskName $AfterCloseTaskName -At '15:30' -Trigger AfterClose

if (Get-ScheduledTask -TaskName $RecoveryTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $RecoveryTaskName -Confirm:$false
}
