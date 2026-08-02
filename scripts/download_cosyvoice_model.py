#!/usr/bin/env python3
"""下载 CosyVoice2-0.5B 模型到本地 cosyvoice-tts/models/ 目录。

用法：
    python scripts/download_cosyvoice_model.py [--cosyvoice-dir DIR] [--model NAME] [--force]

默认目标：<相邻或 OC_PET_COSYVOICE_DIR 指向的 cosyvoice-tts>/models/CosyVoice2-0.5B
模型源：ModelScope 上的 iic/CosyVoice2-0.5B（约 4.6GB，需联网）

依赖：pip install modelscope
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _resolve_cosyvoice_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("OC_PET_COSYVOICE_DIR", "").strip()
    if env:
        return Path(env)
    adjacent = Path(__file__).resolve().parents[1] / "cosyvoice-tts"
    if adjacent.exists():
        return adjacent
    return Path("W:/Games/Hanako/Work/projects/cosyvoice-tts")


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 CosyVoice2-0.5B 模型")
    ap.add_argument("--cosyvoice-dir", default=None,
                    help="cosyvoice-tts 目录（默认：相邻目录 / OC_PET_COSYVOICE_DIR）")
    ap.add_argument("--model", default="CosyVoice2-0.5B",
                    help="模型名（默认 CosyVoice2-0.5B）")
    ap.add_argument("--repo", default="iic/CosyVoice2-0.5B",
                    help="ModelScope 仓库 ID（默认 iic/CosyVoice2-0.5B）")
    ap.add_argument("--force", action="store_true",
                    help="即使目标已存在也重新下载")
    args = ap.parse_args()

    cosy_dir = _resolve_cosyvoice_dir(args.cosyvoice_dir)
    model_dir = cosy_dir / "models" / args.model

    if model_dir.exists() and any(model_dir.iterdir()) and not args.force:
        print(f"[ok] 模型已存在，跳过下载：{model_dir}")
        print("     如需强制重下，加 --force")
        return 0

    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        print("[错误] 未安装 modelscope，请先：pip install modelscope", file=sys.stderr)
        return 2

    print(f"[*] 从 ModelScope 下载 {args.repo} → {model_dir}")
    print("    （约 4.6GB，请保持联网，首次可能较慢）")
    try:
        snapshot_download(
            args.repo,
            local_dir=str(model_dir),
            local_dir_use_symlink=False,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 下载失败：{e}", file=sys.stderr)
        print("      可手动从 https://modelscope.cn/models/iic/CosyVoice2-0.5B "
              "下载后解压到该目录。", file=sys.stderr)
        return 1

    print(f"[ok] 模型已就绪：{model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
