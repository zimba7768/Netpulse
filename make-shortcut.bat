@echo off
rem Puts a NetPulse shortcut on the Desktop, carrying the app's own icon
rem instead of the Python one.
cd /d "%~dp0"
call "%~dp0_find-python.bat"
if not defined PYEXE (
    echo No Python 3.10+ found. Run install.bat first.
    pause
    exit /b 1
)

echo.
echo  Generating netpulse.ico...
"%PYEXE%" "%~dp0main.py" --write-ico

echo  Creating the Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-shortcut.ps1" -Interpreter "%PYWEXE%" -Root "%~dp0"

echo.
echo  Done. You can drag that shortcut onto the taskbar to pin it.
echo.
pause
