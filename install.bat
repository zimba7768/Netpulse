@echo off
title NetPulse - install
cd /d "%~dp0"

echo.
echo  NetPulse - installing dependencies
echo  ==================================
echo.
echo  Looking for Python...

call "%~dp0_find-python.bat"

if not defined PYEXE goto :nopython

echo  Found: %PYEXE%
"%PYEXE%" -c "import sys;print('  Version:', sys.version.split()[0])"
echo.

"%PYEXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo  This Python has no pip. Trying to add it...
    "%PYEXE%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo.
        echo  Could not enable pip for that interpreter.
        echo  Install Python from python.org and run this again.
        echo.
        pause
        exit /b 1
    )
)

echo  Installing PySide6, psutil and watchdog...
echo.
"%PYEXE%" -m pip install --upgrade pip
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  Something went wrong installing the dependencies.
    echo  If you are on a work machine behind a proxy, that is the usual cause.
    echo.
    pause
    exit /b 1
)

echo.
echo  Installing pywintrace (optional - enables the per-app breakdown)...
"%PYEXE%" -m pip install pywintrace
if errorlevel 1 (
    echo.
    echo  pywintrace could not be installed. That is not fatal - NetPulse will
    echo  record machine-wide totals and the file log as normal, and the
    echo  Applications page will explain that it is off.
)

echo.
echo  Checking that everything imports...
"%PYEXE%" -c "import PySide6, psutil, watchdog; print('  PySide6', PySide6.__version__, '- psutil - watchdog: OK')"
if errorlevel 1 (
    echo.
    echo  The packages installed but will not import. This usually means the
    echo  install went to a different Python than the one above.
    echo.
    pause
    exit /b 1
)
"%PYEXE%" -c "import etw" >nul 2>&1
if errorlevel 1 (
    echo   pywintrace: not available - per-app tracking will be off,
    echo               everything else works. See the README.
) else (
    echo   pywintrace: OK - per-app tracking available when run as admin
)

echo.
echo  ==================================
echo  Done.
echo.
echo  Start NetPulse with        run.bat
echo  For per-app tracking use   run-as-admin.bat
echo.
echo  (The interpreter above was saved to python-path.txt, so the run
echo   scripts will use the same one without searching again.)
echo.
pause
exit /b 0

rem ---------------------------------------------------------------------------
:nopython
echo.
echo  No Python 3.10 or newer was found. Searched:
echo    - the "py" launcher
echo    - your PATH
echo    - the Windows registry
echo    - %LOCALAPPDATA%\Programs\Python\Python3*
echo    - %%ProgramFiles%%\Python3*  and  C:\Python3*
echo    - Anaconda / Miniconda in your user folder and ProgramData
echo.

where winget >nul 2>&1
if errorlevel 1 goto :manual

echo  Windows Package Manager (winget) is available on this PC and can
echo  install Python for you - about 30 seconds, no browser needed.
echo.
choice /c YN /m "  Install Python 3.12 now"
if errorlevel 2 goto :manual

echo.
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo  Checking for Python again...
call "%~dp0_find-python.bat"
if defined PYEXE (
    echo  Found: %PYEXE%
    echo.
    echo  Run install.bat once more to finish installing the dependencies.
    echo.
    pause
    exit /b 0
)
echo  Still not found. A sign-out and back in usually fixes this, or see below.
echo.

:manual
echo  Two ways forward:
echo.
echo   1. Install Python from https://www.python.org/downloads/
echo      Tick "Add python.exe to PATH" on the first setup screen.
echo.
echo   2. If Python IS already installed somewhere unusual, put its full path
echo      into a file called python-path.txt next to this script - one line,
echo      for example:
echo         C:\Users\%USERNAME%\anaconda3\python.exe
echo      Then run install.bat again.
echo.
echo   To find it yourself, open a Command Prompt and try:
echo         py -0p
echo      or  dir /s /b C:\python.exe
echo.
pause
exit /b 1
