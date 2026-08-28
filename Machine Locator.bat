@echo off
REM Double-click this file to start Machine Locator.
REM
REM The first run takes a minute while it sets itself up. After that it starts
REM in a couple of seconds. Nothing is installed outside this folder.
REM
REM Flow is written with GOTO labels rather than nested brackets on purpose:
REM batch expands variables inside a bracketed block when it PARSES the block,
REM not when it runs it, which silently breaks this kind of check.

setlocal
cd /d "%~dp0"
title Machine Locator
echo.
echo   Machine Locator
echo   ---------------
echo.

REM ---------------------------------------------------------- find Python
set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto haspython

where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto haspython

REM ------------------------------------------------- offer to install it
echo   Machine Locator needs Python. It's free, and this is the only
echo   thing it needs that isn't already in this folder.
echo.

where winget >nul 2>&1
if errorlevel 1 goto manualpython

echo   Your PC can install it automatically.
echo.
choice /c YN /n /m "   Install Python now? Press Y for yes, N to do it myself: "
if errorlevel 2 goto manualpython

echo.
echo   Installing Python. Windows may ask for permission - say yes.
echo.
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo   ============================================================
echo     Python is installed.
echo.
echo     Now CLOSE this window and double-click Machine Locator
echo     again. Windows only notices new programs in a fresh window.
echo   ============================================================
echo.
pause
exit /b 0

:manualpython
echo.
echo   Install it yourself from:
echo.
echo       https://www.python.org/downloads/
echo.
echo   IMPORTANT: on the first screen of the installer, tick the box
echo   that says "Add Python to PATH". It is easy to miss and nothing
echo   works without it.
echo.
echo   Then double-click Machine Locator again.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:haspython
REM ------------------------------------------------ set up on first run
if exist ".venv\Scripts\mloc.exe" goto run

copy /y nul ".write-test" >nul 2>&1
if not errorlevel 1 goto writable
echo   This folder is read-only, so it can't set itself up.
echo.
echo   It is probably still inside the downloaded zip file. Right-click
echo   the zip, choose "Extract All", and open the folder that comes out.
echo.
pause
exit /b 1

:writable
del ".write-test" >nul 2>&1
echo   First run - setting things up. This takes about a minute.
echo.
if exist ".venv" goto installdeps
%PY% -m venv .venv
if errorlevel 1 goto setupfailed

:installdeps
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -e .
if errorlevel 1 goto setupfailed
echo   Done. That was a one-time step.
echo.

:run
echo   Starting up. Your browser will open by itself.
echo.
".venv\Scripts\mloc.exe" app --port 5000
echo.
echo   Machine Locator has stopped.
pause
exit /b 0

:setupfailed
echo.
echo   Setup failed. This is almost always no internet connection.
echo   Check you are online and try again.
echo.
pause
exit /b 1
