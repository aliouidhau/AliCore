@echo off
rem AliCore launcher (source mode). Works from any drive, no hardcoded paths.
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0ali_app.py"
    exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
    start "" "%~dp0.venv\Scripts\python.exe" "%~dp0ali_app.py"
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0ali_app.py"
    exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    start "" python "%~dp0ali_app.py"
    exit /b 0
)

echo Python not found. Install Python 3.12, or create .venv in this folder.
pause
exit /b 1
