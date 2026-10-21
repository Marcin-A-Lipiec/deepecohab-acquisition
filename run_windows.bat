@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\deepecohab-acquisition.exe" (
    start "" ".venv\Scripts\deepecohab-acquisition.exe"
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_acquisition_gui.py
) else (
    python run_acquisition_gui.py
)
pause
