<#
.SYNOPSIS
    Register an after-close backend launch at 16:35 Taipei time on weekdays.

.DESCRIPTION
    Use -DataDir with the existing data directory used by your desktop or source install.
    -Preview prints the exact task XML, without registering or querying a task.
    The task runs as the current logged-in user and never creates or migrates a data store.

    Remove: .\scripts\install-after-close-schedule.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$Preview,
    [switch]$Replace,
    [string]$DataDir,
    [string]$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = 'Stop'
$TaskName = 'TickStock After Close'
$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Root 'scripts\start-background-backend.ps1'

if ($Remove) {
    if ($Preview -or $Replace) { throw '-Remove cannot be combined with -Preview or -Replace.' }
    if (Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    } else {
        Write-Host "Scheduled task not found: $TaskName"
    }
    exit 0
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    throw 'Pass -DataDir with the same existing data directory that the desktop app uses.'
}
$Store = Get-Item -LiteralPath $DataDir
if ($Store -isnot [System.IO.DirectoryInfo] -or
    -not (Test-Path -LiteralPath (Join-Path $Store.FullName 'user_data') -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $Store.FullName 'kline_daily') -PathType Container)) {
    throw 'DataDir is not an initialized TickStock data directory.'
}
if ($Store.FullName -eq $Store.Root.FullName) { throw 'DataDir must not be a drive root.' }

$PowerShell = Get-Item -LiteralPath $PowerShellPath
if ($PowerShell -isnot [System.IO.FileInfo] -or -not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw 'PowerShell or the background runner is not a file.'
}

$todayInTaipei = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)).ToString(
    'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$boundary = "${todayInTaipei}T16:35:00+08:00"
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -DataDir "{1}"' -f $Runner, $Store.FullName
$commandXml = [Security.SecurityElement]::Escape($PowerShell.FullName)
$argumentsXml = [Security.SecurityElement]::Escape($arguments)
$rootXml = [Security.SecurityElement]::Escape($Root)
$taskXml = @"
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Nanachi Taiwan stock panel: start the after-close backend</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$boundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="CurrentUser">
      <UserId>$userSid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="CurrentUser">
    <Exec>
      <Command>$commandXml</Command>
      <Arguments>$argumentsXml</Arguments>
      <WorkingDirectory>$rootXml</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

if ($Preview) {
    Write-Output $taskXml
    exit 0
}

if (-not (Get-Command 'uv.exe' -CommandType Application -ErrorAction SilentlyContinue)) {
    throw 'uv.exe not found on PATH.'
}
if ((Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue) -and -not $Replace) {
    throw 'Task already exists. Export the existing XML and check -Preview before using -Replace.'
}
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $taskXml -Force:$Replace | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop
Write-Host "Registered scheduled task: $TaskName ($($task.State)), weekdays 16:35 Taipei (UTC+08:00)"
Write-Host 'Remove with: .\scripts\install-after-close-schedule.ps1 -Remove'
exit 0
