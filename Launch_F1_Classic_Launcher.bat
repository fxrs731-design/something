@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo Python 3.8+ is required to run main.py. Download from https://www.python.org/downloads/
    pause
)
