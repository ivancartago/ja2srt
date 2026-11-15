@echo off
cd /d "%~dp0"
echo Setting up ja2srt. This downloads about 2.5 GB of speech models the first time.
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (echo Python was not found. Install Python 3.10 or newer from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
".venv\Scripts\python.exe" setup_models.py
echo.
echo Finished. Double-click "Start ja2srt.bat" to use the app.
pause
