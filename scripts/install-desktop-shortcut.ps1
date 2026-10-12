[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$EntryScript = Join-Path $Root 'scripts\start-desktop.ps1'
if (-not (Test-Path -LiteralPath $EntryScript -PathType Leaf)) {
    throw "找不到正式桌面啟動入口: $EntryScript"
}

$PowerShell = (Get-Command 'powershell.exe' -ErrorAction Stop).Source
# 優先用 Launcher EXE 的圖示；沒有 build EXE 時用專案圖示，避免捷徑看起來像一般 PowerShell。
$Icon = @(
    (Join-Path $Root 'dist\Nanachi台股看板.exe'),
    (Join-Path $Root 'packaging\icon.ico')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

# 桌面、開始功能表、專案資料夾各放一個，任一處都能雙擊啟動。
$Targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) ''),
    $Root
)
$Shell = New-Object -ComObject WScript.Shell
foreach ($Dir in $Targets) {
    $ShortcutPath = Join-Path $Dir 'Nanachi 台股看板.lnk'
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $PowerShell
    $Shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$EntryScript`""
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.Description = 'Nanachi 台股看板正式桌面入口'
    $Shortcut.WindowStyle = 7  # 最小化啟動，PowerShell 視窗不會跳到前景
    if ($Icon) { $Shortcut.IconLocation = "$Icon,0" }
    $Shortcut.Save()
    Write-Host "已建立捷徑: $ShortcutPath"
}
