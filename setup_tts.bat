@echo off
REM 本地 CosyVoice TTS 一键引导（Windows）
REM 用法：双击运行，或在 oc-pet 目录下执行
REM   setup_tts.bat
REM   setup_tts.bat --cosyvoice-repo https://your.git/cosyvoice-tts.git
REM   setup_tts.bat --skip-model   （模型已手动放好时）

cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10~3.12 并加入 PATH。
    pause
    exit /b 1
)

python scripts\setup_tts_env.py %*
if errorlevel 1 (
    echo.
    echo [引导未完全成功] 请查看上方输出。常见处理：
    echo   1. 确认有 NVIDIA 显卡 + 已装驱动（nvidia-smi 能出结果）
    echo   2. 模型下载失败可稍后重跑，或手动从 ModelScope 下载放好
    echo   3. 网络受限时，torch 需手动装 CUDA 版
    pause
    exit /b 1
)

echo.
echo [完成] 本地 CosyVoice TTS 已就绪，直接 start_pet.bat 启动桌宠即可。
pause
