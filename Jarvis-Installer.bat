@echo off
REM ============================================================
REM  JARVIS one-click installer (no manual git clone needed)
REM  Double-click this file. It downloads Jarvis from GitHub,
REM  lets you pick a folder, and installs everything.
REM ============================================================
title Jarvis Installer
color 0B

echo.
echo     ============================================
echo         J A R V I S   -   I N S T A L L E R
echo     ============================================
echo.

REM --- Check for Python ---------------------------------------
where python >nul 2>&1
if %errorlevel%==0 (
    echo [*] Python found. Launching graphical installer...
    echo.
    REM Download installer.py and run it (gives a folder picker + progress).
    powershell -NoProfile -Command ^
      "$u='https://raw.githubusercontent.com/ptcbb4dpf5-oss/jarvis-ai-desktop/main/installer.py';" ^
      "$o=Join-Path $env:TEMP 'jarvis_installer.py';" ^
      "Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing;"
    python "%TEMP%\jarvis_installer.py"
    goto :end
)

echo [!] Python was not found on this PC.
echo     Jarvis needs Python 3.11+ to run.
echo.
echo     Opening the Python download page in your browser...
start https://www.python.org/downloads/
echo.
echo     After installing Python (tick "Add python.exe to PATH"),
echo     run this installer again.
echo.

:end
echo.
pause
