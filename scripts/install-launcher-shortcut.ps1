[CmdletBinding()]
param(
    [string]$LauncherPath = ""
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $LauncherPath) { $LauncherPath = Join-Path $Root 'dist\Nanachi台股看板.exe' }
$LauncherPath = [System.IO.Path]::GetFullPath($LauncherPath)
if (-not (Test-Path -LiteralPath $LauncherPath -PathType Leaf)) {
    throw "找不到 Launcher EXE: $LauncherPath。請先執行 scripts\build-launcher.ps1。"
}

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Nanachi Windows Launcher.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $LauncherPath
$shortcut.WorkingDirectory = Split-Path -Parent $LauncherPath
$shortcut.Description = 'Nanachi 台股看板服務管理與除錯工具'
$shortcut.IconLocation = "$LauncherPath,0"
$shortcut.Save()
Write-Host "已建立桌面捷徑: $shortcutPath"
