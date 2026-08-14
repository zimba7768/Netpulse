@echo off
cd /d "%~dp0"
call "%~dp0_find-python.bat"
if not defined PYWEXE (
    echo No Python 3.10+ found. Run install.bat first.
    pause
    exit /b 1
)
start "" "%PYWEXE%" "%~dp0main.py" %*
