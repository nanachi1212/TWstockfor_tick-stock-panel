<#
.SYNOPSIS
    讓「Nanachi 台股看板」後端在 Windows 登入時自動啟動 (常駐盯盤、16:30 自動更新)。

.DESCRIPTION
    在目前使用者的「啟動」資料夾建立捷徑，登入後以隱藏視窗執行 scripts\start-desktop.ps1。
    不需要系統管理員權限、不改登錄檔、不建立排程工作。

    移除自動啟動: .\scripts\install-autostart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$EntryScript = Join-Path $Root 'scripts\start-desktop.ps1'
$StartupDir = [Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupDir 'Nanachi 台股看板 (自動啟動).lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $ShortcutPath -PathType Leaf) {
        Remove-Item -LiteralPath $ShortcutPath -Force
        Write-Host "已移除自動啟動: $ShortcutPath"
    } else {
        Write-Host '目前沒有自動啟動捷徑，無需移除。'
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $EntryScript -PathType Leaf)) {
    throw "找不到桌面啟動入口: $EntryScript"
}
if ([string]::IsNullOrWhiteSpace($StartupDir) -or -not (Test-Path -LiteralPath $StartupDir -PathType Container)) {
    throw '找不到 Windows 啟動資料夾，無法建立自動啟動捷徑。'
}

$PowerShell = (Get-Command 'powershell.exe' -ErrorAction Stop).Source
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$EntryScript`""
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = 'Nanachi 台股看板：登入後自動啟動後端，常駐盯盤與 16:30 自動更新'
$Shortcut.WindowStyle = 7
$Shortcut.Save()

if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
    throw '捷徑建立失敗。'
}
Write-Host "已建立自動啟動捷徑: $ShortcutPath"
Write-Host '下次登入 Windows 時會自動在背景啟動後端；要取消請執行: .\scripts\install-autostart.ps1 -Remove'
exit 0
