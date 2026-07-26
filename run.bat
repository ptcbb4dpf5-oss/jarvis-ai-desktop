@echo off
REM Launch JARVIS using the local virtual environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python main.py
