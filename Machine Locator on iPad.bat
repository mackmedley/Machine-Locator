@echo off
REM Opens Machine Locator to your own Wi-Fi so you can use it on an iPad,
REM iPhone, or any other device in the house.
REM
REM This computer does the work; the iPad just displays it. Both have to be
REM on the same Wi-Fi, and this window has to stay open.

cd /d "%~dp0"
title Machine Locator - on your iPad
echo.
echo   Machine Locator -- on your iPad
echo   -------------------------------
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
    echo   It's a free download: https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: tick "Add Python to PATH" in the installer.
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

REM --- set up on first run --------------------------------------------------
if not exist ".venv\Scripts\mloc.exe" (
    copy /y nul ".write-test" >nul 2>&1
    if errorlevel 1 (
        echo   This folder is read-only, so it can't set itself up.
        echo.
        echo   It is probably still inside the downloaded .zip. Right-click
        echo   the zip, choose "Extract All", and open the folder that
        echo   comes out.
        echo.
        pause
        exit /b 1
    )
    del ".write-test" >nul 2>&1

    echo   First run -- setting things up. This takes a minute.
    echo.
    if not exist ".venv" (
        %PY% -m venv .venv
        if errorlevel 1 goto setupfailed
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e .
    if errorlevel 1 goto setupfailed
    echo   Done. That was a one-time step.
)

REM --- show the address, then serve it --------------------------------------
".venv\Scripts\python.exe" -m machine_locator.lan 5000

echo   Windows may ask whether to allow this through the firewall.
echo   Tick "Private networks" and click Allow access -- the iPad
echo   cannot reach it otherwise.
echo.
echo   The first time, it will ask you to pick a password. Do that on
echo   the iPad, then it remembers you.
echo.

".venv\Scripts\mloc.exe" serve --host 0.0.0.0 --port 5000
echo.
echo   Machine Locator has stopped.
pause
exit /b 0

:setupfailed
echo.
echo   Setup failed. This is almost always no internet connection.
echo.
pause
exit /b 1
