@echo off
rem Double-click entry: starts the desktop app, same as the desktop shortcut.
start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start-desktop.ps1"
