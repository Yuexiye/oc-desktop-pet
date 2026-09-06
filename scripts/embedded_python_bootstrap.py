"""嵌入式 Python 引导 — 随包分发嵌入式 Python，双击即用

竞品参考（FaustBot）：`embedded_python_bootstrap.py` + `setup-runtime.bat`
随包分发嵌入式 Python，双击即用。

决策点：oc-pet 已有 PyInstaller spec，二者选其一即可。
- 若 PyInstaller 打包已稳定，本项降级为"验证打包产物无需预装 Python"
- 若需要嵌入式 Python，使用本脚本

功能：
- 检测嵌入式 Python 是否存在
- 自动解压嵌入式 Python（如需要）
- 设置环境变量
- 启动应用
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

logger = None  # 延迟导入，避免嵌入式 Python 缺少 logging

DEFAULT_PYTHON_VERSION = "3.12.4"
DEFAULT_EMBEDDED_URL = (
    f"https://www.python.org/ftp/python/{DEFAULT_PYTHON_VERSION}/python-{DEFAULT_PYTHON_VERSION}-embed-amd64.zip"
)

SCRIPT_DIR = Path(__file__).parent
PYTHON_DIR = SCRIPT_DIR / "python-embedded"
APP_SCRIPT = SCRIPT_DIR / "main.py"


def get_logger():
    """获取 logger（延迟导入）"""
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("embedded_bootstrap")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(handler)
    return logger


def check_embedded_python() -> bool:
    """检查嵌入式 Python 是否存在"""
    return (PYTHON_DIR / "python.exe").exists()


def download_embedded_python(url: str = DEFAULT_EMBEDDED_URL) -> bool:
    """下载嵌入式 Python"""
    log = get_logger()
    log.info("Downloading embedded Python from %s", url)
    
    try:
        zip_path = SCRIPT_DIR / "python-embedded.zip"
        urllib.request.urlretrieve(url, zip_path)
        log.info("Downloaded %d bytes", zip_path.stat().st_size)
        return True
    except Exception as e:
        log.error("Download failed: %s", e)
        return False


def extract_embedded_python(zip_path: Path | None = None) -> bool:
    """解压嵌入式 Python"""
    log = get_logger()
    
    if zip_path is None:
        zip_path = SCRIPT_DIR / "python-embedded.zip"
    
    if not zip_path.exists():
        log.error("Zip file not found: %s", zip_path)
        return False
    
    log.info("Extracting embedded Python to %s", PYTHON_DIR)
    
    try:
        PYTHON_DIR.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(PYTHON_DIR)
        
        log.info("Extraction completed")
        
        # 删除 zip 文件
        zip_path.unlink()
        
        return True
    except Exception as e:
        log.error("Extraction failed: %s", e)
        return False


def setup_embedded_python() -> bool:
    """设置嵌入式 Python 环境变量"""
    log = get_logger()
    
    python_exe = PYTHON_DIR / "python.exe"
    if not python_exe.exists():
        log.error("Python executable not found: %s", python_exe)
        return False
    
    # 设置 PATH
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(PYTHON_DIR) + os.pathsep + old_path
    
    # 设置 PYTHONHOME
    os.environ["PYTHONHOME"] = str(PYTHON_DIR)
    
    # 设置 PYTHONPATH
    site_packages = PYTHON_DIR / "Lib" / "site-packages"
    if site_packages.exists():
        os.environ["PYTHONPATH"] = str(site_packages) + os.pathsep + os.environ.get("PYTHONPATH", "")
    
    log.info("Environment setup completed")
    return True


def install_pip() -> bool:
    """安装 pip（嵌入式 Python 默认没有 pip）"""
    log = get_logger()
    
    python_exe = PYTHON_DIR / "python.exe"
    if not python_exe.exists():
        return False
    
    # 下载 get-pip.py
    get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
    get_pip_path = SCRIPT_DIR / "get-pip.py"
    
    log.info("Downloading get-pip.py")
    try:
        urllib.request.urlretrieve(get_pip_url, get_pip_path)
    except Exception as e:
        log.error("Failed to download get-pip.py: %s", e)
        return False
    
    # 执行 get-pip.py
    log.info("Installing pip")
    try:
        import subprocess
        result = subprocess.run(
            [str(python_exe), str(get_pip_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            log.error("pip install failed: %s", result.stderr)
            return False
    except Exception as e:
        log.error("pip install failed: %s", e)
        return False
    
    # 删除 get-pip.py
    get_pip_path.unlink()
    
    log.info("pip installed successfully")
    return True


def install_requirements() -> bool:
    """安装依赖"""
    log = get_logger()
    
    python_exe = PYTHON_DIR / "python.exe"
    if not python_exe.exists():
        return False
    
    requirements_path = SCRIPT_DIR / "requirements.txt"
    if not requirements_path.exists():
        log.warning("requirements.txt not found, skipping")
        return False
    
    log.info("Installing requirements")
    try:
        import subprocess
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "-r", str(requirements_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            log.error("requirements install failed: %s", result.stderr)
            return False
    except Exception as e:
        log.error("requirements install failed: %s", e)
        return False
    
    log.info("Requirements installed successfully")
    return True


def run_app() -> int:
    """运行应用"""
    log = get_logger()
    
    python_exe = PYTHON_DIR / "python.exe"
    if not python_exe.exists():
        log.error("Python executable not found")
        return 1
    
    if not APP_SCRIPT.exists():
        log.error("Application script not found: %s", APP_SCRIPT)
        return 1
    
    log.info("Starting application: %s", APP_SCRIPT)
    
    try:
        import subprocess
        result = subprocess.run(
            [str(python_exe), str(APP_SCRIPT)],
            cwd=str(SCRIPT_DIR),
        )
        return result.returncode
    except Exception as e:
        log.error("Failed to run application: %s", e)
        return 1


def bootstrap() -> int:
    """引导流程"""
    log = get_logger()
    log.info("Embedded Python bootstrap started")
    
    # 1. 检查嵌入式 Python 是否存在
    if not check_embedded_python():
        log.info("Embedded Python not found, downloading...")
        
        # 2. 下载
        if not download_embedded_python():
            log.error("Failed to download embedded Python")
            return 1
        
        # 3. 解压
        if not extract_embedded_python():
            log.error("Failed to extract embedded Python")
            return 1
    
    # 4. 设置环境变量
    if not setup_embedded_python():
        log.error("Failed to setup embedded Python")
        return 1
    
    # 5. 检查 pip 是否存在（site-packages 目录）
    site_packages = PYTHON_DIR / "Lib" / "site-packages"
    if not site_packages.exists():
        log.info("site-packages not found, installing pip...")
        if not install_pip():
            log.error("Failed to install pip")
            return 1
        
        # 6. 安装依赖
        if not install_requirements():
            log.error("Failed to install requirements")
            return 1
    
    # 7. 运行应用
    return run_app()


def main():
    """主入口"""
    try:
        return bootstrap()
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())