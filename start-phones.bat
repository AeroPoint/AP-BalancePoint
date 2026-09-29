@echo off
rem Like start.bat, but your phones can open the app too, over Tailscale (see README: "On your phone").
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run start.bat once first to set things up.
  pause
  goto :eof
)
.venv\Scripts\python.exe run.py serve --phones
