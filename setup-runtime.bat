@echo off
REM OC Pet Runtime Setup Script
REM 双击此文件启动嵌入式 Python 引导流程

title OC Pet Runtime Setup
color 0A

echo ============================================
echo   OC Pet Runtime Setup
echo ============================================
echo.

REM 检查 Python 是否存在
where python >nul 2>nul
if %errorlevel% == 0 (
    echo [INFO] System Python detected, skipping embedded Python setup
    echo [INFO] Running application with system Python...
    python main.py
    goto :end
)

REM 检查嵌入式 Python 是否存在
if exist "python-embedded\python.exe" (
    echo [INFO] Embedded Python found
    goto :run
)

REM 下载嵌入式 Python
echo [INFO] Downloading embedded Python...
python -c "from scripts.embedded_python_bootstrap import bootstrap; exit(bootstrap())"
goto :end

:run
echo [INFO] Running application...

:run
python-embedded\python.exe main.py

:end
echo.
echo [DONE] Application exited
pause