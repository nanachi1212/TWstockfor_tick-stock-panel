[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendPython = if ($Python) { $Python } else { Join-Path $Root 'backend\.venv\Scripts\python.exe' }

if (-not (Test-Path -LiteralPath $BackendPython)) {
    throw "找不到 Python 環境: $BackendPython。請先依 README 建立 backend/.venv。"
}

& $BackendPython -c "import tkinter; tkinter.Tcl(useTk=False)"
if ($LASTEXITCODE -ne 0) {
    throw '目前 Python 環境缺少可用的 Tcl/Tk runtime，無法可靠打包 tkinter GUI。請改用含 Tcl/Tk 的既有 Python，並以 -Python 指定。'
}

Push-Location $Root
try {
    & $BackendPython -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) { throw '目前 Python 環境未安裝 PyInstaller。請在既有 backend/.venv 安裝後重試。' }
    & $BackendPython -m PyInstaller --onefile --windowed --clean --noconfirm `
        --name 'Nanachi台股看板' --icon (Join-Path $Root 'packaging\icon.ico') `
        (Join-Path $Root 'scripts\windows_launcher.py')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher EXE build failed.' }
    Write-Host "完成: $(Join-Path $Root 'dist\Nanachi台股看板.exe')"
}
finally {
    Pop-Location
}
