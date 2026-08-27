@echo off
chcp 65001 >nul
title Building Duplicate Cleaner EXE
cd /d "%~dp0.."

set "PYEXE="
for %%V in (3.12 3.13 3.11 3.10 3) do (
    if not defined PYEXE (
        py -%%V -c "import tkinter" >nul 2>&1 && set "PYEXE=py -%%V"
    )
)
if not defined PYEXE (
    echo Python with tkinter was not found.
    pause
    exit /b 1
)

echo [1/4] Installing build dependencies...
%PYEXE% -m pip install --quiet --upgrade pyinstaller mutagen send2trash pillow || goto :fail

echo [2/4] Generating icon...
%PYEXE% build_tools\make_icon.py || goto :fail

echo [3/4] Building single-file executable...
%PYEXE% -m PyInstaller --noconfirm --clean build_tools\DuplicateCleaner.spec || goto :fail

echo [4/4] Done.
echo.
echo Executable: "%CD%\dist\DuplicateCleaner.exe"
explorer "%CD%\dist"
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
