#!/usr/bin/env python3
"""本地 CosyVoice TTS 一键引导（从零部署）。

帮新用户把本地 TTS 跑起来，全程幂等、可重跑：
  1. 建 venv（或复用 --python 指定的解释器）
  2. 装 torch(CUDA 12.x) + 依赖（requirements_cosyvoice.txt）
  3. 获取 cosyvoice-tts 代码（克隆 --cosyvoice-repo，或复用相邻的 ../cosyvoice-tts）
  4. 下载 CosyVoice2-0.5B 模型（约 4.6GB，需联网）
  5. 写 .env（OC_PET_COSYVOICE_DIR / OC_PET_COSYVOICE_PYTHON）

用法：
  python scripts/setup_tts_env.py
  python scripts/setup_tts_env.py --cosyvoice-repo https://your.git/cosyvoice-tts.git
  python scripts/setup_tts_env.py --venv .venv_cosy --skip-model   # 模型已手动放好时

前置：Windows + NVIDIA 显卡 + 驱动；Python 3.10~3.12。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def log(step: str, msg: str = "") -> None:
    bar = "=" * 60
    if msg:
        print(f"\n{bar}\n[{step}] {msg}\n{bar}")
    else:
        print(f"\n{bar}\n[{step}]\n{bar}")


def run(python: str, args: list[str], **kw) -> int:
    cmd = [python, *args]
    print("+", " ".join(cmd))
    return subprocess.call(cmd, **kw)


def ensure_venv(venv_dir: Path, target_py: str | None) -> str:
    """返回用于后续安装的解释器路径。"""
    if target_py:
        return target_py
    venv_py = (venv_dir / "Scripts" / "python.exe") if os.name == "nt" \
        else (venv_dir / "bin" / "python")
    if venv_dir.exists() and venv_py.exists():
        log("venv", f"复用已有 venv: {venv_dir}")
        return str(venv_py)
    log("venv", f"创建 venv: {venv_dir}")
    subprocess.check_call([PY, "-m", "venv", str(venv_dir)])
    return str(venv_py)


def install_torch(python: str) -> None:
    log("torch", "安装 CUDA 版 torch（来自 pytorch cu12x 源，约数百 MB）")
    # 官方 CosyVoice2 推荐 torch>=2.5；cu124 是验证最充分的 CUDA 12 组合。
    # 不装 PyPI 默认 torch（无 CUDA），否则本地 TTS 会跑 CPU（每句 60-150s）。
    code = run(python, [
        "-m", "pip", "install", "--index-url",
        "https://download.pytorch.org/whl/cu124",
        "torch==2.5.1+cu124", "torchaudio==2.5.1+cu124",
    ])
    if code != 0:
        print("[警告] torch 安装失败，可手动：pip install torch torchaudio "
              "--index-url https://download.pytorch.org/whl/cu124", file=sys.stderr)


def install_deps(python: str) -> None:
    req = ROOT / "requirements_cosyvoice.txt"
    log("deps", f"安装其余依赖：{req.name}")
    code = run(python, ["-m", "pip", "install", "-r", str(req)])
    if code != 0:
        print("[警告] 依赖安装返回非 0，请检查上方输出", file=sys.stderr)


def acquire_cosyvoice(python: str, repo_url: str | None) -> Path | None:
    """返回 cosyvoice-tts 目录；找不到且无法克隆则返回 None。"""
    # 1) env
    env_dir = os.environ.get("OC_PET_COSYVOICE_DIR", "").strip()
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)
    # 2) 相邻目录
    adjacent = ROOT / ".." / "cosyvoice-tts"
    if adjacent.exists():
        log("cosyvoice-tts", f"复用相邻目录: {adjacent}")
        return adjacent.resolve()
    # 3) 克隆
    if not repo_url:
        log("cosyvoice-tts",
            "未找到 cosyvoice-tts，且未提供 --cosyvoice-repo。\n"
            "      请手动把 cosyvoice-tts 放到 oc-pet 的同级目录，或在 .env 设置 "
            "OC_PET_COSYVOICE_DIR。")
        return None
    target = (ROOT / ".." / "cosyvoice-tts").resolve()
    log("cosyvoice-tts", f"克隆 {repo_url} → {target}")
    try:
        subprocess.check_call(["git", "clone", repo_url, str(target)])
        return target
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 克隆失败：{e}", file=sys.stderr)
        return None


def write_env(cosy_dir: Path, python: str) -> None:
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text("utf-8", errors="ignore").splitlines()
    # 去掉旧的同名变量
    lines = [ln for ln in lines
             if not ln.startswith("OC_PET_COSYVOICE_DIR=")
             and not ln.startswith("OC_PET_COSYVOICE_PYTHON=")]
    lines.append(f"OC_PET_COSYVOICE_DIR={cosy_dir}")
    lines.append(f"OC_PET_COSYVOICE_PYTHON={python}")
    env_path.write_text("\n".join(lines) + "\n", "utf-8")
    log("env", f"已写入 .env：OC_PET_COSYVOICE_DIR / OC_PET_COSYVOICE_PYTHON")


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 CosyVoice TTS 引导")
    ap.add_argument("--cosyvoice-repo", default=None,
                    help="cosyvoice-tts 的 git 地址（默认：复用相邻目录）")
    ap.add_argument("--venv", default=str(ROOT / ".venv_cosy"),
                    help="目标 venv 路径（默认 .venv_cosy）")
    ap.add_argument("--python", default=None,
                    help="直接指定解释器（跳过建 venv）")
    ap.add_argument("--skip-deps", action="store_true", help="跳过 pip 安装")
    ap.add_argument("--skip-model", action="store_true", help="跳过模型下载")
    args = ap.parse_args()

    log("开始", "本地 CosyVoice TTS 引导")

    python = ensure_venv(Path(args.venv), args.python)
    if not args.skip_deps:
        install_torch(python)
        install_deps(python)

    cosy_dir = acquire_cosyvoice(python, args.cosyvoice_repo)
    if cosy_dir is None:
        print("[错误] 无法定位 cosyvoice-tts，引导中止。", file=sys.stderr)
        return 1

    if not args.skip_model:
        log("model", "下载 CosyVoice2-0.5B 模型（约 4.6GB，需联网）")
        dl = ROOT / "scripts" / "download_cosyvoice_model.py"
        code = run(python, [str(dl), "--cosyvoice-dir", str(cosy_dir)])
        if code != 0:
            print("[警告] 模型下载失败，可稍后手动运行该脚本。", file=sys.stderr)

    write_env(cosy_dir, python)

    log("完成",
        "本地 CosyVoice TTS 已就绪。\n"
        "  启动桌宠：start_pet.bat （或 python main.py）\n"
        "  无 NVIDIA 显卡时，本地 TTS 会自动给出告警，可在「设置 → TTS」"
        "改用 MIMO / 在线 TTS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
