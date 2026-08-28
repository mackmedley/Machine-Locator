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
    echo.
)

echo   If Windows asks whether to allow this through the firewall, tick
echo   "Private networks" and click Allow access. The iPad cannot reach
echo   it otherwise.
echo.

REM --- start serving, then prove the iPad can actually reach it -------------
REM Binding to the Wi-Fi and being reachable over it are different things,
REM so this checks the second one and prints what is wrong if it fails,
REM rather than leaving a blank page on the iPad with no explanation.
".venv\Scripts\python.exe" -m machine_locator.lan --serve

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
