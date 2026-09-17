@echo off
chcp 65001 >nul
title Duplicate Cleaner
cd /d "%~dp0"

rem Find a Python that can import the standard library bits we rely on.
set "PYEXE="
for %%V in (3.12 3.13 3.11 3.10 3) do (
    if not defined PYEXE (
        py -%%V -c "import sqlite3, concurrent.futures" >nul 2>&1 && set "PYEXE=py -%%V"
    )
)

if not defined PYEXE (
    echo Python 3.10 or newer was not found. Install it from python.org
    pause
    exit /b 1
)

rem pywebview draws the window through the Edge WebView2 runtime.
%PYEXE% -c "import webview" >nul 2>&1 || (
    echo Installing required packages...
    %PYEXE% -m pip install --quiet pywebview mutagen send2trash
)
%PYEXE% -c "import mutagen, send2trash" >nul 2>&1 || (
    echo Installing optional packages...
    %PYEXE% -m pip install --quiet mutagen send2trash
)

rem the py launcher runs .pyw files with pythonw.exe, so no console window appears
start "" %PYEXE% "%~dp0run.pyw"
