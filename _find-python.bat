@echo off
rem ---------------------------------------------------------------------------
rem  Locates a Python 3.10+ interpreter and sets PYEXE / PYWEXE for the caller.
rem  Called (not run directly) by install.bat, run.bat and run-as-admin.bat.
rem
rem  Deliberately no SETLOCAL: the variables must survive back to the caller.
rem  Search order: saved choice -> py launcher -> PATH -> registry -> the usual
rem  install locations (python.org, Anaconda, Miniconda).
rem ---------------------------------------------------------------------------
set "PYEXE="
set "PYWEXE="
set "PYFIND_DIR=%~dp0"

rem --- 0) a path saved by a previous run, or written by hand -----------------
if exist "%PYFIND_DIR%python-path.txt" (
    for /f "usebackq delims=" %%I in ("%PYFIND_DIR%python-path.txt") do call :np_try "%%I"
)
if defined PYEXE goto :np_found

rem --- 1) the py launcher (present even when "Add to PATH" was skipped) ------
for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :np_try "%%I"
if defined PYEXE goto :np_found

rem --- 2) whatever is on PATH ------------------------------------------------
for /f "delims=" %%I in ('where python 2^>nul') do call :np_try "%%I"
for /f "delims=" %%I in ('where python3 2^>nul') do call :np_try "%%I"
if defined PYEXE goto :np_found

rem --- 3) the registry (python.org installers record this) -------------------
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul') do call :np_try "%%B"
for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul') do call :np_try "%%B"
if defined PYEXE goto :np_found

rem --- 4) common install locations -------------------------------------------
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :np_try "%%D\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*")                do call :np_try "%%D\python.exe"
for /d %%D in ("C:\Python3*")                            do call :np_try "%%D\python.exe"
call :np_try "%USERPROFILE%\anaconda3\python.exe"
call :np_try "%USERPROFILE%\miniconda3\python.exe"
call :np_try "%USERPROFILE%\Anaconda3\python.exe"
call :np_try "%LOCALAPPDATA%\anaconda3\python.exe"
call :np_try "%LOCALAPPDATA%\Continuum\anaconda3\python.exe"
call :np_try "%ProgramData%\anaconda3\python.exe"
call :np_try "%ProgramData%\Anaconda3\python.exe"
call :np_try "%ProgramData%\miniconda3\python.exe"
if defined PYEXE goto :np_found

rem Nothing usable found. PYEXE stays empty; the caller reports it.
goto :eof

rem ---------------------------------------------------------------------------
:np_try
rem Accept the candidate only if it exists AND really is Python 3.10 or newer.
if defined PYEXE goto :eof
set "NP_CAND=%~1"
if "%NP_CAND%"=="" goto :eof
if not exist "%NP_CAND%" goto :eof
rem Skip the Microsoft Store stub - "running" it just opens the Store.
set "NP_STRIPPED=%NP_CAND:WindowsApps=%"
if not "%NP_STRIPPED%"=="%NP_CAND%" goto :eof
"%NP_CAND%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PYEXE=%NP_CAND%"
goto :eof

rem ---------------------------------------------------------------------------
:np_found
rem pythonw.exe is the console-free twin; fall back to python.exe if absent.
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"
> "%PYFIND_DIR%python-path.txt" echo %PYEXE%
goto :eof
