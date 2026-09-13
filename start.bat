@echo off
rem Double-click to start the budget app. First run creates the virtual environment.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Python environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
)
.venv\Scripts\python.exe run.py serve
goto :eof

:error
echo Setup failed. Make sure Python 3.11+ is installed and on PATH.
pause
