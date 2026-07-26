@echo off
REM ===================================================================
REM  JARVIS v1 - Windows setup script
REM  Creates a virtual environment, installs all dependencies,
REM  downloads the Playwright Chromium browser, and prepares config.
REM ===================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ============================================
echo    JARVIS v1  -  Setup
echo  ============================================
echo.

REM --- 1. Check Python -------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on your PATH.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    echo         and make sure "Add Python to PATH" is checked.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Found Python %PYVER%

REM --- 2. Create virtual environment ---------------------------------
if not exist ".venv" (
    echo [..] Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [OK] Virtual environment already exists.
)

call ".venv\Scripts\activate.bat"

REM --- 3. Upgrade pip -------------------------------------------------
echo [..] Upgrading pip ...
python -m pip install --upgrade pip setuptools wheel

REM --- 4. Install requirements ---------------------------------------
echo [..] Installing Python dependencies (this may take a few minutes) ...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [WARN] Some packages failed to install.
    echo        If PyAudio failed, try:  python -m pip install pipwin ^&^& pipwin install pyaudio
    echo.
)

REM --- 5. Install Playwright browser ---------------------------------
echo [..] Installing Playwright Chromium browser ...
python -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright browser install failed. Browser automation may not work.
)

REM --- 6. Prepare folders --------------------------------------------
if not exist "plugins" mkdir plugins
if not exist "screenshots" mkdir screenshots
if not exist "config" mkdir config

echo.
echo  ============================================
echo    Setup complete!
echo  ============================================
echo.
echo  NEXT STEPS:
echo    1. Set your LLM API key (recommended - environment variable):
echo          setx OPENAI_API_KEY "sk-your-key-here"
echo       ...or paste it into config\settings.json  ("llm" ^> "api_key").
echo.
echo    2. Launch JARVIS:
echo          run.bat
echo       ...or manually:  .venv\Scripts\activate  ^&^&  python main.py
echo.
pause
endlocal
