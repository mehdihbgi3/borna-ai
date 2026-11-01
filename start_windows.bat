@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo [X] Run install_windows.bat first.
  pause
  exit /b 1
)
title Borna pricing server - keep this window open
".venv\Scripts\python.exe" -m borna_pricing serve
pause
