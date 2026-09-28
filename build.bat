@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm ace_scheduler.spec
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m tools.package_release
if errorlevel 1 goto failed
echo Build ready: dist\ACE-Scheduler\ACE-Scheduler.exe
exit /b 0
:failed
echo Build failed. See the error above.
pause
exit /b 1
