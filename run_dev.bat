@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m ace_scheduler %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo ACE Scheduler could not start. See the error above.
pause
exit /b 1
