@echo off
rem Starts NetPulse elevated so the kernel network trace can be opened,
rem which is what makes the per-application breakdown possible.
cd /d "%~dp0"
call "%~dp0_find-python.bat"
if not defined PYWEXE (
    echo No Python 3.10+ found. Run install.bat first.
    pause
    exit /b 1
)
rem main.py has no spaces, so it needs no quoting inside the argument list;
rem the folder is handled by -WorkingDirectory instead.
powershell -NoProfile -Command "Start-Process -FilePath '%PYWEXE%' -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -Verb RunAs"
