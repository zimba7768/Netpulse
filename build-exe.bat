@echo off
setlocal EnableExtensions
title NetPulse - build NetPulse.exe
cd /d "%~dp0"

echo.
echo  Building NetPulse.exe
echo  =====================
echo.

call "%~dp0_find-python.bat"
if not defined PYEXE (
    echo  No Python 3.10+ found. Run install.bat first.
    pause
    exit /b 1
)
echo  Using: %PYEXE%
echo.

echo  Making sure the build tools are present...
"%PYEXE%" -m pip install --upgrade --quiet pyinstaller
"%PYEXE%" -m pip install --quiet -r requirements.txt
"%PYEXE%" -m pip install --quiet pywintrace

echo  Refreshing the icon...
"%PYEXE%" main.py --write-ico

echo.
echo  Running PyInstaller (this takes a minute or two)...
"%PYEXE%" -m PyInstaller --noconfirm --clean netpulse.spec
if errorlevel 1 (
    echo.
    echo  The build failed - the output above says why.
    pause
    exit /b 1
)

echo.
echo  =====================
echo  Done: dist\NetPulse.exe
echo.
for %%F in ("dist\NetPulse.exe") do echo  Size: %%~zF bytes
echo.
echo  That single file is the whole application - no Python needed on the
echo  machine you copy it to. Windows SmartScreen will warn the first time
echo  it runs because the file is not code-signed; "More info" then
echo  "Run anyway" gets past it.
echo.
pause
