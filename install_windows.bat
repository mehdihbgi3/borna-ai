@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
chcp 65001 >nul

echo === Borna pricing: installation ===
echo.

set "PY="
if not "%~1"=="" (
  call :try "%~1"
  if not defined PY (
    echo [X] "%~1" is not a working Python 3.10 or newer.
    echo.
    goto :notfound
  )
)
if not defined PY call :try py -3
if not defined PY call :try py
if not defined PY call :try python
if not defined PY call :try python3
if not defined PY for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%LocalAppData%\Python\pythoncore-3*" "%ProgramFiles%\Python3*" "C:\Python3*" "D:\Python3*" "%UserProfile%\anaconda3" "%UserProfile%\miniconda3" "%LocalAppData%\anaconda3" "%LocalAppData%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "D:\anaconda3" "D:\miniconda3") do if not defined PY if exist "%%~D\python.exe" call :try "%%~D\python.exe"
if not defined PY goto :notfound

echo Python found:
%PY% -c "import sys; print('  ' + sys.executable); print('  Python ' + sys.version.split()[0])" <nul
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" <nul >nul 2>nul || rmdir /s /q ".venv"
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating the virtual environment...
  %PY% -m venv .venv <nul
  if errorlevel 1 goto :venvfail
)

echo Installing packages from the wheels folder - no internet needed...
".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt <nul
if errorlevel 1 goto :pipfail
echo.
echo Creating settings.ini, the database, the base price list and the admin user...
".venv\Scripts\python.exe" -m borna_pricing init
if errorlevel 1 goto :fail
echo.
echo Done. Next: run test_api_windows.bat, then start_windows.bat
pause
exit /b 0

:notfound
echo [X] Python 3.10 or newer was not found.
echo     Searched: py -3, py, python, python3 and the usual install folders
echo     (python.org, the Python install manager, Anaconda, Miniconda).
echo.
echo What this computer reports:
echo --- where py
where py 2>&1
echo --- where python
where python 2>&1
echo --- py --version
py --version <nul 2>&1
echo --- python --version
python --version <nul 2>&1
echo.
echo How to fix, either:
echo   1. Install Python 3.12 from python.org, tick "Add python.exe to PATH", run this file again.
echo   2. Drag your python.exe onto this file, or run:
echo      install_windows.bat "C:\full\path\to\python.exe"
pause
exit /b 1

:venvfail
echo [X] Could not create the virtual environment with this Python.
echo     Install Python 3.12 from python.org, or run this file with another python.exe path.
pause
exit /b 1

:pipfail
echo [X] Installing packages failed. Supported Python versions: 3.10 to 3.15.
echo     Send a screenshot of the messages above to support.
pause
exit /b 1

:fail
echo [X] Installation failed. Send a screenshot of the messages above to support.
pause
exit /b 1

:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" <nul >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
