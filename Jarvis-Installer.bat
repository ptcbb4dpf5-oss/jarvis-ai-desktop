@echo off
REM ============================================================
REM  JARVIS one-click installer for a BRAND-NEW Windows PC.
REM  Double-click this file. It will:
REM    1. Detect a REAL Python (ignoring the Microsoft Store stub)
REM    2. Auto-download + silently install Python 3.11 if missing
REM    3. Download Jarvis from GitHub (you pick the folder)
REM    4. Install every dependency + a desktop shortcut
REM  No manual clone, no pre-installed Python required.
REM ============================================================
setlocal enabledelayedexpansion
title Jarvis Installer
color 0B

echo.
echo     ============================================
echo         J A R V I S   -   I N S T A L L E R
echo     ============================================
echo.

set "PYVERSION=3.11.9"
set "PYCMD="

REM ---------- 1. Detect a real Python ------------------------
REM The 'py' launcher is never shadowed by the Microsoft Store stub,
REM so we try it first.
py -3 --version >nul 2>&1 && set "PYCMD=py -3"

REM Fall back to 'python', but VERIFY it really prints "Python 3.x"
REM (the Store stub prints an error instead, so findstr won't match).
if not defined PYCMD (
    for /f "delims=" %%v in ('python --version 2^>nul') do (
        echo %%v | findstr /b /c:"Python 3" >nul 2>&1 && set "PYCMD=python"
    )
)

if defined PYCMD (
    echo [*] Found Python: 
    %PYCMD% --version
    echo.
    goto :have_python
)

REM ---------- 2. Auto-install Python -------------------------
echo [!] No real Python detected on this PC.
echo [*] Downloading Python %PYVERSION% (about 25 MB)...
set "PYINST=%TEMP%\python-%PYVERSION%-amd64.exe"
powershell -NoProfile -Command ^
  "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVERSION%/python-%PYVERSION%-amd64.exe' -OutFile '%PYINST%' -UseBasicParsing } catch { exit 1 }"

if not exist "%PYINST%" (
    echo [X] Could not download Python. Check your internet connection.
    echo     You can install Python manually from https://www.python.org/downloads/
    start https://www.python.org/downloads/
    goto :end
)

echo [*] Installing Python silently (per-user, no admin needed)...
echo     This can take a couple of minutes - please wait.
"%PYINST%" /quiet PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0

REM Give the installer a moment to finish registering.
timeout /t 3 /nobreak >nul

REM ---------- Re-detect Python after install -----------------
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PYCMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    )
)
if not defined PYCMD (
    if exist "%ProgramFiles%\Python311\python.exe" (
        set "PYCMD=%ProgramFiles%\Python311\python.exe"
    )
)

if not defined PYCMD (
    echo [X] Python was installed but could not be located automatically.
    echo     Please CLOSE this window and run Jarvis-Installer.bat again -
    echo     it will detect Python on the second run.
    goto :end
)

echo [OK] Python installed successfully:
%PYCMD% --version
echo.

:have_python
REM ---------- 3. Download and launch the installer -----------
echo [*] Downloading the Jarvis installer...
set "JINST=%TEMP%\jarvis_installer.py"
powershell -NoProfile -Command ^
  "try { Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/ptcbb4dpf5-oss/jarvis-ai-desktop/main/installer.py' -OutFile '%JINST%' -UseBasicParsing } catch { exit 1 }"

if not exist "%JINST%" (
    echo [X] Could not download the Jarvis installer. Check your connection.
    goto :end
)

echo [*] Launching the graphical installer (pick your folder there)...
echo.
%PYCMD% "%JINST%"

:end
echo.
echo Press any key to close this window.
pause >nul
endlocal
