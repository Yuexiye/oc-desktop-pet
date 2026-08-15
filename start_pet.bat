@echo off
cd /d "%~dp0"
echo Starting OC Desktop Pet (with auto-restart watchdog)...

:: ??? venv
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py
    goto :end
)

:: ????? Python
where python >nul 2>&1
if %errorlevel%==0 (
    python launcher.py
    goto :end
)

echo [ERROR] Python not found! Please install Python 3.10+ and add to PATH.
pause

:end
