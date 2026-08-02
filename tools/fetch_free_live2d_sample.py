"""获取免费 Cubism Live2D 示例模型（Haru）到 characters/sample_live2d/live2d/

来源：Live2D 官方 CubismWebSamples（Live2D Open Software License，非商用免费，
需保留 LICENSE）。本脚本仅复制模型文件，不修改其许可证。

用法：
    python tools/fetch_free_live2d_sample.py [模型名]
    模型名可选：haru(默认) / hiyori / mark / natori / rice
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO_URL = "https://github.com/Live2D/CubismWebSamples.git"
VALID = ["haru", "hiyori", "mark", "natori", "rice"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", nargs="?", default="haru", choices=VALID)
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = os.path.join(here, "characters", "sample_live2d", "live2d")
    os.makedirs(dest, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        print(f"[fetch] 克隆 CubismWebSamples (shallow) ...")
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, os.path.join(tmp, "repo")],
            check=True,
        )
        src = os.path.join(tmp, "repo", "Samples", "Resources", args.model.capitalize())
        if not os.path.isdir(src):
            # 部分模型目录名与参数大小写不同，兜底扫描
            res = os.path.join(tmp, "repo", "Samples", "Resources")
            for name in os.listdir(res):
                if name.lower() == args.model.lower():
                    src = os.path.join(res, name)
                    break
        if not os.path.isdir(src):
            print(f"[fetch] 未找到模型目录: {args.model}", file=sys.stderr)
            sys.exit(1)

        # 清空旧模型并复制
        for f in os.listdir(dest):
            p = os.path.join(dest, f)
            if os.path.isfile(p):
                os.remove(p)
            else:
                shutil.rmtree(p, ignore_errors=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)

        # 写 pet.json 标记（含 live2d 缩放/偏移覆盖位，可手动微调）
        pet_json = os.path.join(here, "characters", "sample_live2d", "pet.json")
        if not os.path.exists(pet_json):
            with open(pet_json, "w", encoding="utf-8") as fh:
                fh.write(
                    '{\n  "name": "Sample Live2D (Haru)",\n'
                    '  "live2d": { "scale": 1.0, "offset": [0, 0] }\n}\n'
                )

        print(f"[fetch] 已复制 {args.model} -> {dest}")
        print(f"[fetch] 完成。运行 oc-pet 并选择角色 'sample_live2d' 即可看到 Live2D 角色。")


if __name__ == "__main__":
    main()
