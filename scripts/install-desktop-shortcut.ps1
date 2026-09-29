[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$EntryScript = Join-Path $Root 'scripts\start-desktop.ps1'
if (-not (Test-Path -LiteralPath $EntryScript -PathType Leaf)) {
    throw "找不到正式桌面啟動入口: $EntryScript"
}

$PowerShell = (Get-Command 'powershell.exe' -ErrorAction Stop).Source
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'Nanachi 台股看板.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$EntryScript`""
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = 'Nanachi 台股看板正式桌面入口'
$Shortcut.WindowStyle = 1
$Icon = Join-Path $Root 'dist\Nanachi台股看板.exe'
if (Test-Path -LiteralPath $Icon -PathType Leaf) {
    $Shortcut.IconLocation = "$Icon,0"
}
$Shortcut.Save()
Write-Host "已建立桌面捷徑: $ShortcutPath"
