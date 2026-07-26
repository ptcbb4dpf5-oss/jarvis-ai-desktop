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

REM --- 1. Check Python (ignoring the Microsoft Store stub) ------------
REM The 'py' launcher is never shadowed by the Store stub, so try it first.
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    for /f "delims=" %%v in ('python --version 2^>nul') do (
        echo %%v | findstr /b /c:"Python 3" >nul 2>&1 && set "PYCMD=python"
    )
)
if not defined PYCMD (
    echo [ERROR] A real Python 3 was not found.
    echo         Either run Jarvis-Installer.bat ^(auto-installs Python^), or
    echo         install Python 3.11+ from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

for /f "delims=" %%v in ('%PYCMD% --version 2^>^&1') do set PYVER=%%v
echo [OK] Found %PYVER%

REM --- 2. Create virtual environment ---------------------------------
if not exist ".venv" (
    echo [..] Creating virtual environment in .venv ...
    %PYCMD% -m venv .venv
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
