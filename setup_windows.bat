@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -c requirements.txt .
if errorlevel 1 goto failed
echo DeepEcoHab Acquisition and its Python dependencies installed.
echo For FLIR cameras, install the matching Spinnaker/PySpin package and add FFmpeg to PATH.
echo Launch with run_windows.bat.
echo Or create an icon: .venv\Scripts\deepecohab-acquisition-shortcut.exe
pause
exit /b 0
:failed
echo Setup failed. Read the error above and the instructions in README.md.
pause
exit /b 1
