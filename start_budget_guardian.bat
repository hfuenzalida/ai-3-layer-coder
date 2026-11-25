@echo off
:: Self-locating — works regardless of where the project folder lives.
:: %~dp0 always expands to the directory containing this .bat file.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo [ERROR] pythonw.exe not found at %~dp0.venv\Scripts\
    echo         Re-create the virtualenv: python -m venv .venv
    echo         Then reinstall: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\pythonw.exe" "%~dp0budget_guardian.py"
