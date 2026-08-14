@echo off
setlocal EnableExtensions
title NetPulse - publish to GitHub
cd /d "%~dp0"

echo.
echo  Publish NetPulse to GitHub
echo  ==========================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo  Git is not installed. Get it from https://git-scm.com/download/win
    echo  then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".git" (
    echo  This folder is not a git repository.
    echo  Use the copy that came with the .git folder included.
    echo.
    pause
    exit /b 1
)

echo  FIRST: create an EMPTY repository on github.com
echo    - go to https://github.com/new
echo    - name it (netpulse is the obvious choice)
echo    - do NOT tick "Add a README", ".gitignore" or "Choose a license"
echo      (this folder already has all three - ticking them causes a conflict)
echo    - click "Create repository"
echo.
pause

echo.
set /p GHUSER=  Your GitHub username:
if "%GHUSER%"=="" (
    echo  No username entered - nothing done.
    pause
    exit /b 1
)

set "GHREPO="
set /p GHREPO=  Repository name [netpulse]:
if "%GHREPO%"=="" set "GHREPO=netpulse"

echo.
echo  Pointing the README badges at github.com/%GHUSER%/%GHREPO% ...
powershell -NoProfile -Command ^
  "$t = Get-Content -Raw 'README.md';" ^
  "$t = $t -replace 'YOUR-USERNAME/netpulse', '%GHUSER%/%GHREPO%';" ^
  "$t = $t -replace 'YOUR-USERNAME', '%GHUSER%';" ^
  "[IO.File]::WriteAllText((Resolve-Path 'README.md'), $t)"

git add README.md
git diff --cached --quiet || git commit -m "Point README badges at the published repository"

echo.
echo  Adding the remote and pushing...
git remote remove origin >nul 2>&1
git remote add origin "https://github.com/%GHUSER%/%GHREPO%.git"
git branch -M main
git push -u origin main
if errorlevel 1 (
    echo.
    echo  The push failed. The usual causes:
    echo    - the repository does not exist yet on github.com
    echo    - you are not signed in; Git should open a browser window to
    echo      authenticate, so watch for it behind this one
    echo    - the repository was created WITH a README, so the histories
    echo      differ; either delete and recreate it empty, or run:
    echo         git pull --rebase origin main
    echo         git push -u origin main
    echo.
    pause
    exit /b 1
)

echo.
echo  Done. Your repository is at https://github.com/%GHUSER%/%GHREPO%
echo.
echo  The tests workflow will start running there within a minute - the
echo  badge at the top of the README turns green when it passes.
echo.
pause
