@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
".venv\Scripts\python.exe" -m borna_pricing test-api
pause
