@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Installation did not finish. The message above explains what needs attention.
  pause
  exit /b 1
)
echo.
echo Press any key to open Stele.
pause >nul
start "" "%USERPROFILE%\.stele\app\.venv\Scripts\stele.exe" ui
