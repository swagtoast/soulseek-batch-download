@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PATH=%PROJECT_DIR%\bin;%PROJECT_DIR%\runtime\python;%PATH%"

set "PYTHON_BIN="
if exist "%PROJECT_DIR%\runtime\python\python.exe" (
    set "PYTHON_BIN=%PROJECT_DIR%\runtime\python\python.exe"
) else (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_BIN set "PYTHON_BIN=%%I"
    )
)

if not defined PYTHON_BIN (
    echo ERRORE: Python 3 non trovato. Riesegui il comando di installazione PowerShell.
    pause
    exit /b 1
)

if "%~1"=="" (
    "%PYTHON_BIN%" "%PROJECT_DIR%\src\cli.py" --pause
) else (
    "%PYTHON_BIN%" "%PROJECT_DIR%\src\cli.py" %*
)
exit /b %ERRORLEVEL%
