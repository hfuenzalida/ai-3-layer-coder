@echo off
:: ============================================================
::  Codex Futura — REPL Launcher
::  Double-click to open the 3-layer coding assistant.
::  Keep this window open while you work; close it to exit.
:: ============================================================
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo.
    echo  [Codex Futura] ERROR: .venv not found.
    echo  Run:  python -m venv .venv  ^&  pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo  =========================================
echo   Codex Futura REPL  ^|  3-Layer Assistant
echo  =========================================
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0codex.py"
echo.
echo  Session ended.
pause
