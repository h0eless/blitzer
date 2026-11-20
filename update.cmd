@echo off
rem update.cmd - manual refresh: double-click this file.
rem Runs the same pipeline as the scheduled task and keeps the window open,
rem so a failed run shows its message instead of flashing and closing.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0daily.ps1"
echo.
if %ERRORLEVEL% NEQ 0 (
    echo FEHLER: Exit-Code %ERRORLEVEL%. Details: logs\error.log
) else (
    echo Fertig.
)
pause >nul
