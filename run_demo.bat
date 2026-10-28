@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements-desktop.txt
)
set /p PORT=COM port (default COM5): 
if "%PORT%"=="" set PORT=COM5
.venv\Scripts\python receiver_cli.py --port %PORT% --demo
pause
