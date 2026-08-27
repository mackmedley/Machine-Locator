@echo off
REM Double-click this file to start Machine Locator on Windows.
REM
REM The first run takes a minute while it sets itself up; after that it starts
REM in a couple of seconds. Nothing is installed outside this folder.

cd /d "%~dp0"
title Machine Locator
echo.
echo   Machine Locator
echo   ---------------
echo.

REM --- find a usable Python -------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo   Python isn't installed.
    echo.
    echo   Machine Locator needs it to run. It's a free download:
    echo.
    echo       https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: on the first screen of the installer, tick the box
    echo   that says "Add Python to PATH". It is easy to miss and nothing
    echo   works without it.
    echo.
    echo   Then double-click this file again.
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

REM --- set up the private environment on first run --------------------------
if not exist ".venv\Scripts\mloc.exe" (
    REM Running straight out of the .zip leaves the folder read-only, which
    REM otherwise fails deep inside pip with nothing useful on screen.
    copy /y nul ".write-test" >nul 2>&1
    if errorlevel 1 (
        echo   This folder is read-only, so it can't set itself up.
        echo.
        echo   This usually means it is still inside the downloaded .zip.
        echo   Right-click the zip, choose "Extract All", and open the
        echo   folder that comes out.
        echo.
        pause
        exit /b 1
    )
    del ".write-test" >nul 2>&1

    echo   First run -- setting things up. This takes a minute.
    echo.
    if not exist ".venv" (
        echo   Creating a private Python environment...
        %PY% -m venv .venv
        if errorlevel 1 goto setupfailed
    )
    echo   Installing Machine Locator and what it needs...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e .
    if errorlevel 1 goto setupfailed
    echo   Done. That was a one-time step.
    echo.
)

echo   Starting up...
echo.
".venv\Scripts\mloc.exe" app --port 5000
echo.
echo   Machine Locator has stopped.
pause
exit /b 0

:setupfailed
echo.
echo   Setup failed. This is almost always no internet connection.
echo   Check your connection and try again.
echo.
pause
exit /b 1
