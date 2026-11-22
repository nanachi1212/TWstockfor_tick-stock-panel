<#
.SYNOPSIS
    Register a scheduled task that starts the backend at 16:35 on weekdays when it is not running.

.DESCRIPTION
    The formal selection lock and the weekly ledger are written by the after-close run, and the
    weekly ledger can only be written on the week's last session before midnight. When the app is
    closed that run never happens. This task (current user, only while logged on, no admin rights)
    runs scripts\start-background-backend.ps1, which exits at once if a backend is already up.
    StartWhenAvailable runs a missed start once the PC is on again; the backend's own deadline
    checks decide what can still be recorded.

    Remove: .\scripts\install-after-close-schedule.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [string]$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = 'Stop'
$TaskName = 'TickStock After Close'
$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Root 'scripts\start-background-backend.ps1'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    } else {
        Write-Host "Scheduled task not found: $TaskName"
    }
    exit 0
}

foreach ($path in @($PowerShellPath, $Runner)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "File not found: $path" }
}
if (-not (Get-Command 'uv.exe' -ErrorAction SilentlyContinue)) { throw 'uv.exe not found on PATH.' }

$arguments = "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Runner`""
$action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At '16:35'
# The started backend keeps running (scheduler for 21:30 / 23:00), so no execution time limit.
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Nanachi Taiwan stock panel: start the backend after the close when it is not running' -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Write-Host "Registered scheduled task: $TaskName ($($task.State)), weekdays 16:35"
Write-Host 'Remove with: .\scripts\install-after-close-schedule.ps1 -Remove'
exit 0
