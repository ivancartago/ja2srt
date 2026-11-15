@echo off
cd /d "%~dp0"
echo Starting ja2srt. Keep this window open while you use it; close it to stop.
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" app.py) else (python app.py)
pause
