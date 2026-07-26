@echo off
REM ============================================================
REM  Build JarvisInstaller.exe from installer.py using PyInstaller.
REM  Run this ONCE on a Windows PC to produce a shareable .exe.
REM  Output: dist\JarvisInstaller.exe
REM ============================================================
title Build Jarvis Installer
color 0B

echo.
echo  Building JarvisInstaller.exe ...
echo.

where python >nul 2>&1
if not %errorlevel%==0 (
    echo [!] Python not found. Install Python 3.11+ first.
    pause
    exit /b 1
)

echo [*] Installing PyInstaller if needed...
python -m pip install --upgrade pyinstaller

echo [*] Compiling...
python -m PyInstaller --onefile --noconsole --name JarvisInstaller installer.py

echo.
if exist dist\JarvisInstaller.exe (
    echo [OK] Done!  Your installer is here:
    echo       %cd%\dist\JarvisInstaller.exe
    echo.
    echo  Share/keep that single .exe - double-clicking it installs Jarvis
    echo  to any folder the user picks, no Python-cloning required.
) else (
    echo [X] Build failed. Check the messages above.
)
echo.
pause
