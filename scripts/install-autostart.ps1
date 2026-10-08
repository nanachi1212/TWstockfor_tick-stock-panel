<#
.SYNOPSIS
    讓「Nanachi 台股看板」後端在 Windows 登入時自動在背景啟動 (常駐盯盤、16:30 自動更新)。

.DESCRIPTION
    在目前使用者的「啟動」資料夾建立捷徑，登入後以隱藏視窗只啟動後端 (uvicorn, 127.0.0.1:3018)，
    不會跳出桌面視窗。之後點桌面捷徑開啟看板時，桌面程式會直接連到這個已在執行的後端，
    關掉視窗也不會停掉背景盯盤。
    不需要系統管理員權限、不改登錄檔、不建立排程工作。

    移除自動啟動: .\scripts\install-autostart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$BackendPort = 3018   # 與 app.desktop 的 _BASE_PORT 一致, 桌面程式會沿用已在執行的後端
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

if (-not (Test-Path -LiteralPath (Join-Path $BackendDir 'app\main.py') -PathType Leaf)) {
    throw "找不到後端程式: $BackendDir\app\main.py"
}
$Uv = Get-Command 'uv' -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw '找不到 uv，請先安裝 uv 並確認 PATH 後再執行。'
}
if ([string]::IsNullOrWhiteSpace($StartupDir) -or -not (Test-Path -LiteralPath $StartupDir -PathType Container)) {
    throw '找不到 Windows 啟動資料夾，無法建立自動啟動捷徑。'
}

# 只跑後端, 以隱藏視窗的 PowerShell 包一層 (uv/uvicorn 子程序共用同一個隱藏主控台, 不會跳出黑窗):
#   uv run --project backend python -m uvicorn app.main:app --host 127.0.0.1 --port 3018
$PowerShell = (Get-Command 'powershell.exe' -ErrorAction Stop).Source
$BackendCmd = "& '$($Uv.Source)' run --project '$BackendDir' python -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$BackendCmd`""
$Shortcut.WorkingDirectory = $BackendDir
$Shortcut.Description = 'Nanachi 台股看板：登入後在背景啟動後端 (無視窗)，常駐盯盤與 16:30 自動更新'
$Shortcut.WindowStyle = 7
$Shortcut.Save()

if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
    throw '捷徑建立失敗。'
}
Write-Host "已建立自動啟動捷徑: $ShortcutPath"
Write-Host "下次登入 Windows 時會在背景啟動後端 (127.0.0.1:$BackendPort，無視窗)；桌面捷徑開啟看板會直接沿用它。"
Write-Host '要取消請執行: .\scripts\install-autostart.ps1 -Remove'
exit 0
