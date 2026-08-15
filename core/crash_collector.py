"""崩溃现场自动收集器。

目标：桌宠崩溃（尤其是 0x8001010d 这类 C 层 COM 错误，Python traceback 抓不到）
时，自动把"破案线索"打包成一个 zip，方便事后定位根因，而不必再靠猜。

收集内容：
    - crash_trace.txt        （faulthandler 抓的 C 层栈，若已生成）
    - oc_pet.log 尾部 2000 行（最近运行上下文）
    - sys.modules 中所有 C 扩展（.pyd/.so）列表 —— 用来锁定"谁偷偷初始化了 COM"
    - 当前存活线程快照（线程名 + 堆栈）
    - 环境信息（Python 版本、解释器路径、启动参数）

触发时机：
    - atexit（正常/异常退出都会跑，但 segfault 不一定能跑）
    - 增强的 sys.excepthook（Python 异常退出必跑）
    - faulthandler 的 dump 回调（C 层崩溃时，在写栈之后调一次）

注意：本模块只做"收集"，不负责重启——重启由 launcher.py 父进程负责。
"""
from __future__ import annotations

import os
import sys
import time
import atexit
import traceback
import threading
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_COLLECTED = False


def _project_root() -> Path:
    # main.py 在仓库根；本文件在 core/ 下
    return Path(__file__).resolve().parent.parent


def _collect_once(reason: str) -> str | None:
    """执行一次收集，返回生成的 zip 路径或 None。幂等（只跑一次）。"""
    global _COLLECTED
    if _COLLECTED:
        return None
    _COLLECTED = True

    root = _project_root()
    logs_dir = root / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    zip_path = logs_dir / f"crash_dump_{ts}.zip"

    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1) crash_trace.txt
            ct = root / "crash_trace.txt"
            if ct.exists():
                zf.write(ct, "crash_trace.txt")

            # 2) oc_pet.log 尾部
            logfile = logs_dir / "oc_pet.log"
            if logfile.exists():
                try:
                    with open(logfile, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    tail = "".join(lines[-2000:])
                    zf.writestr("oc_pet.log.tail.txt", tail)
                except Exception as e:
                    zf.writestr("oc_pet.log.tail.txt", f"(读取失败: {e})")

            # 3) C 扩展列表（锁定 COM 嫌疑）
            c_exts = []
            for name, mod in list(sys.modules.items()):
                try:
                    fn = getattr(mod, "__file__", None)
                    if fn and (fn.endswith(".pyd") or fn.endswith(".so") or ".pyd" in fn):
                        c_exts.append(f"{name}\t{fn}")
                except Exception:
                    pass
            # 也列出可疑的 COM 相关顶层模块是否曾被导入
            com_suspects = [m for m in sys.modules
                            if m.split(".")[0] in
                            ("pythoncom", "win32com", "pywin", "comtypes", "ctypes")]
            c_exts.append("")
            c_exts.append("=== COM 相关模块是否曾被导入 ===")
            c_exts.append("\n".join(com_suspects) if com_suspects else "(无)")
            zf.writestr("c_extensions.txt", "\n".join(c_exts))

            # 4) 线程快照
            threads = []
            for t in threading.enumerate():
                threads.append(f"[thread] {t.name} (daemon={t.daemon}, alive={t.is_alive()})")
            zf.writestr("threads.txt", "\n".join(threads))

            # 5) 环境信息
            env = [
                f"reason={reason}",
                f"time={ts}",
                f"python={sys.version}",
                f"executable={sys.executable}",
                f"argv={sys.argv}",
                f"platform={sys.platform}",
            ]
            zf.writestr("env.txt", "\n".join(env))

        log.warning("崩溃现场已打包: %s", zip_path)
        return str(zip_path)
    except Exception as e:
        try:
            log.error("崩溃收集失败: %s\n%s", e, traceback.format_exc())
        except Exception:
            pass
        return None


def install(reason_default: str = "unknown") -> None:
    """在 main.py 早期调用，安装所有钩子。"""
    # atexit：正常/Python 异常退出都会触发（segfault 不一定）
    try:
        atexit.register(lambda: _collect_once("atexit"))
    except Exception:
        pass

    # 增强 excepthook：Python 未捕获异常时必跑
    _orig = sys.excepthook
    def _hook(etype, exc, tb):
        try:
            _collect_once(f"python_exc:{etype.__name__}")
        except Exception:
            pass
        _orig(etype, exc, tb)
    try:
        sys.excepthook = _hook
    except Exception:
        pass

    # 注：faulthandler 的 C 层 dump 时机无法注入 Python 回调，但它会把
    # crash_trace.txt 写到磁盘；C 层崩溃时 atexit 不一定跑，因此我们在
    # install() 末尾显式检查"上次遗留的 crash_trace.txt"并补打包（见下）。
    try:
        _collect_stale_crash()
    except Exception:
        pass


def _collect_stale_crash() -> str | None:
    """启动时检查上次崩溃遗留的 crash_trace.txt（C 层崩溃 atexit 未跑的情况），
    若有则补打包，避免线索丢失。"""
    root = _project_root()
    ct = root / "crash_trace.txt"
    if not ct.exists():
        return None
    # 仅当文件较旧（非本次运行刚生成）才视为上次的遗留
    try:
        age = time.time() - ct.stat().st_mtime
        if age < 30:  # 30s 内视为当前运行期，跳过
            return None
    except Exception:
        pass
    return _collect_once("stale_crash_trace_on_startup")
