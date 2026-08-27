@echo off
chcp 65001 >nul
title Duplicate Cleaner
cd /d "%~dp0"

set "PYEXE="
for %%V in (3.12 3.13 3.11 3.10 3) do (
    if not defined PYEXE (
        py -%%V -c "import tkinter" >nul 2>&1 && set "PYEXE=py -%%V"
    )
)

if not defined PYEXE (
    echo Python with tkinter was not found. Please install Python 3.12 from python.org
    pause
    exit /b 1
)

%PYEXE% -c "import mutagen, send2trash" >nul 2>&1 || (
    echo Installing required packages...
    %PYEXE% -m pip install --quiet mutagen send2trash
)

rem the py launcher runs .pyw files with pythonw.exe, so no console window appears
start "" %PYEXE% "%~dp0run.pyw"
