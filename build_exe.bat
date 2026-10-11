@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   PDFMaker - Standalone EXE Build Script (Windows 11)
echo ========================================================
echo.

echo [1/4] Checking Python virtual environment (.venv)...
if not exist ".venv" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo [2/4] Installing required libraries via pip...
call .\.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    pause
    exit /b 1
)

call .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo [3/4] Running automated tests (pytest)...
call .\.venv\Scripts\python.exe -m pytest -v
if errorlevel 1 (
    echo [ERROR] Tests failed. Build aborted.
    pause
    exit /b 1
)

echo.
echo [4/4] Building standalone EXE with PyInstaller...
call .\.venv\Scripts\python.exe -m PyInstaller --onefile --noconsole --clean --icon=app.ico --add-data "app.ico;." --name "PDFMaker" app.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   BUILD SUCCESSFUL!
echo   Output: dist\PDFMaker.exe
echo ========================================================
echo.
pause
