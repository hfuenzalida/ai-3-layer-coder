@echo off
:: ============================================================
::  Codex Futura — Ecosystem Launcher
::  --------------------------------------------------------
::  Starts two services every time you run it:
::
::    [1] Budget Guardian  — silent background watchdog
::                           monitors CSV spend, applies lock
::                           files at 80 %% / 100 %% of budget
::
::    [2] Codex Monitor    — floating dark GUI window
::                           shows layer health (green/red dots)
::                           + live streaming output from the REPL
::
::  Autostart on login:
::    python setup_autostart.py          <- installs this file
::    python setup_autostart.py --verify <- checks paths are valid
::    python setup_autostart.py --remove <- uninstalls
::
::  Manual use:
::    Double-click this file at any time to (re-)start services.
:: ============================================================
cd /d "%~dp0"

:: ── Guard: make sure the virtual environment exists ──────────
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo.
    echo  [Codex Futura] ERROR: .venv\Scripts\pythonw.exe not found.
    echo.
    echo  The virtual environment is missing or incomplete.
    echo  Open a terminal in this folder and run:
    echo.
    echo    python -m venv .venv
    echo    .venv\Scripts\pip install -r requirements.txt
    echo.
    echo  Then double-click this file again.
    echo.
    pause
    exit /b 1
)

:: ── [1] Budget Guardian — silent, no console window ─────────
::  /b keeps it attached to this session but windowless;
::  pythonw.exe suppresses the console entirely.
echo  [1/2] Starting Budget Guardian (background)...
start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0budget_guardian.py"

:: Short pause so guardian has time to bind before monitor reads locks
ping -n 2 127.0.0.1 >nul

:: ── [2] Codex Monitor — Tkinter GUI, stays visible ──────────
::  pythonw.exe + Tkinter = GUI window, no black console behind it.
::  The monitor floats top-right and auto-refreshes every 30 s.
echo  [2/2] Starting Codex Monitor (floating window)...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0monitor.py"

:: ── Done ─────────────────────────────────────────────────────
echo.
echo  [Codex Futura] Ecosystem is running.
echo.
echo    Budget Guardian  ^>  silent background process
echo    Codex Monitor    ^>  floating window, top-right of screen
echo.
echo  To open the REPL:
echo    * Double-click  start_repl.bat
echo    * Or open a terminal here and run:  python codex.py
echo.
timeout /t 5 /nobreak >nul
exit /b 0
