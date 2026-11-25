@echo off
:: Self-locating — works regardless of where the project folder lives.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\streamlit.exe" (
    echo [ERROR] streamlit.exe not found at %~dp0.venv\Scripts\
    echo         Re-create the virtualenv: python -m venv .venv
    echo         Then reinstall: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\streamlit.exe" run "%~dp0dashboard.py"
