#!/usr/bin/env python3
"""procedural_emotion_frames.py

基于现有 idle 帧，用 Pillow 程序生成 yuexinmiao 的情绪反应帧与 TTS 口型帧。

生成目录：
    characters/yuexinmiao/frames/<anim>/frame_00.png

对应动画：
    angry / surprise / sleep / eat / speak_open / speak_half / speak_closed

设计原则：
    - 自动检测蓝色眼睛位置作为面部锚点。
    - 所有表情覆盖层使用与精灵表一致的深棕色描边/线条。
    - 保持 192×208 RGBA 透明底，风格统一。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAR_DIR = PROJECT_ROOT / "characters" / "yuexinmiao"
FRAMES_DIR = CHAR_DIR / "frames"
IDLE_FRAME = FRAMES_DIR / "idle" / "idle_0.png"

# 配色与角色描边保持一致
BROWN = (90, 65, 50, 255)
DARK_BROWN = (60, 42, 32, 255)
WHITE = (255, 255, 255, 255)
BLACK = (40, 35, 35, 255)
RED = (255, 90, 90, 255)
DARK_RED = (180, 50, 50, 255)
PINK = (255, 180, 190, 200)
BLUE = (80, 140, 220, 255)
LIGHT_BLUE = (160, 200, 240, 200)
MOUTH_INSIDE = (120, 60, 55, 255)


def load_idle() -> Image.Image:
    if not IDLE_FRAME.exists():
        raise FileNotFoundError(f"找不到基准 idle 帧: {IDLE_FRAME}")
    return Image.open(IDLE_FRAME).convert("RGBA")


def detect_eyes(img: Image.Image) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """自动检测两个蓝色眼睛中心，返回 (left_eye, right_eye)。"""
    px = img.load()
    W, H = img.size
    blue_points = []
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            if a > 128 and b > 120 and r < 80 and g < 100 and b > r + 40 and b > g + 40:
                blue_points.append((x, y))
    if not blue_points:
        # 回退到画面中心
        cx, cy = W // 2, H // 3
        return (cx - 24, cy), (cx + 24, cy)

    xs = [p[0] for p in blue_points]
    mx = sorted(xs)[len(xs) // 2]
    left_pts = [p for p in blue_points if p[0] <= mx]
    right_pts = [p for p in blue_points if p[0] > mx]

    def center(pts):
        return sum(p[0] for p in pts) // len(pts), sum(p[1] for p in pts) // len(pts)

    return center(left_pts), center(right_pts)


def landmarks(img: Image.Image):
    """计算面部关键点。"""
    left, right = detect_eyes(img)
    eye_y = (left[1] + right[1]) // 2
    face_cx = (left[0] + right[0]) // 2
    eye_dist = right[0] - left[0]
    # 嘴部在两眼连线下方约 0.55 倍眼距处，营造 chibi 比例
    mouth_y = int(eye_y + eye_dist * 0.55)
    mouth = (face_cx, mouth_y)
    return {
        "left_eye": left,
        "right_eye": right,
        "eye_dist": eye_dist,
        "face_cx": face_cx,
        "eye_y": eye_y,
        "mouth": mouth,
    }


def new_layer(size: Tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def ellipse_bb(center: Tuple[int, int], rx: int, ry: int):
    x, y = center
    return [x - rx, y - ry, x + rx, y + ry]


def draw_angry(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    ex = lm["eye_dist"] // 2
    ey = lm["eye_y"] - ex // 3

    # 愤怒眉毛：向中心下方倾斜的粗线
    brow_w = ex // 3
    brow_h = ex // 5
    brow_y = ey - ex // 2
    # 左眉
    d.line(
        [(lm["left_eye"][0] - brow_w, brow_y - brow_h),
         (lm["left_eye"][0] + brow_w, brow_y + brow_h)],
        fill=DARK_BROWN, width=4, joint="curve"
    )
    # 右眉
    d.line(
        [(lm["right_eye"][0] + brow_w, brow_y - brow_h),
         (lm["right_eye"][0] - brow_w, brow_y + brow_h)],
        fill=DARK_BROWN, width=4, joint="curve"
    )

    # 生气纹（额头红色小折线）
    fx = lm["face_cx"]
    fy = brow_y - ex // 2
    d.line([(fx - 6, fy - 4), (fx, fy + 2), (fx + 6, fy - 4)],
           fill=DARK_RED, width=3)

    # 皱眉嘴（向下弧线）
    mx, my = lm["mouth"]
    mw = ex // 4
    d.arc([mx - mw, my - mw // 2, mx + mw, my + mw],
          start=200, end=340, fill=DARK_BROWN, width=4)

    # 上半脸红晕（低透明红色叠加）
    overlay = new_layer(out.size)
    od = ImageDraw.Draw(overlay)
    od.ellipse([lm["face_cx"] - ex, ey - ex, lm["face_cx"] + ex, ey + ex],
               fill=(255, 60, 60, 40))
    out = Image.alpha_composite(out, overlay)
    out = Image.alpha_composite(out, layer)
    return out


def draw_surprise(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    ex = lm["eye_dist"] // 2

    # 大白眼 + 小黑瞳
    for eye in (lm["left_eye"], lm["right_eye"]):
        d.ellipse(ellipse_bb(eye, 9, 13), fill=WHITE, outline=DARK_BROWN, width=3)
        d.ellipse(ellipse_bb((eye[0], eye[1] + 3), 4, 5), fill=BLACK)

    # 惊讶 O 型嘴
    mx, my = lm["mouth"]
    d.ellipse(ellipse_bb((mx, my + 2), 8, 10), fill=MOUTH_INSIDE, outline=DARK_BROWN, width=3)
    d.ellipse(ellipse_bb((mx - 2, my - 1), 2, 2), fill=WHITE)

    # 小感叹号
    d.line([(lm["face_cx"] + ex + 4, lm["eye_y"] - ex - 6),
            (lm["face_cx"] + ex + 4, lm["eye_y"] - ex - 14)],
           fill=DARK_BROWN, width=3)
    d.ellipse(ellipse_bb((lm["face_cx"] + ex + 4, lm["eye_y"] - ex - 2), 2, 2), fill=DARK_BROWN)

    out = Image.alpha_composite(out, layer)
    return out


def draw_sleep(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    ex = lm["eye_dist"] // 2
    ey = lm["eye_y"]

    # 闭眼的弧线
    arc_r = ex // 4
    for eye in (lm["left_eye"], lm["right_eye"]):
        d.arc([eye[0] - arc_r, eye[1] - arc_r // 2,
               eye[0] + arc_r, eye[1] + arc_r],
              start=200, end=340, fill=DARK_BROWN, width=4)

    # 小 o 嘴
    mx, my = lm["mouth"]
    d.ellipse(ellipse_bb((mx, my), 3, 3), fill=MOUTH_INSIDE, outline=DARK_BROWN, width=2)

    # 睡泡 / Z
    zx = lm["face_cx"] + ex + 8
    zy = lm["eye_y"] - ex
    d.text((zx, zy), "Z", fill=LIGHT_BLUE)
    d.text((zx + 10, zy - 14), "z", fill=(LIGHT_BLUE[0], LIGHT_BLUE[1], LIGHT_BLUE[2], 140))

    # 淡蓝睡意 overlay
    overlay = new_layer(out.size)
    od = ImageDraw.Draw(overlay)
    od.ellipse([lm["face_cx"] - ex, ey - ex, lm["face_cx"] + ex, ey + ex],
               fill=(120, 180, 255, 25))
    out = Image.alpha_composite(out, overlay)
    out = Image.alpha_composite(out, layer)
    return out


def draw_eat(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    ex = lm["eye_dist"] // 2
    mx, my = lm["mouth"]

    # 咀嚼的椭圆嘴（半开）
    d.ellipse([mx - 8, my - 3, mx + 8, my + 9],
              fill=MOUTH_INSIDE, outline=DARK_BROWN, width=3)

    # 食物碎屑
    d.ellipse(ellipse_bb((mx + 12, my + 6), 3, 3), fill=(220, 160, 80, 255), outline=DARK_BROWN, width=1)

    # 腮红
    for dx in (-ex - 6, ex + 6):
        d.ellipse(ellipse_bb((lm["face_cx"] + dx, lm["eye_y"] + ex // 2), 7, 5), fill=PINK)

    out = Image.alpha_composite(out, layer)
    return out


def draw_speak_open(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    mx, my = lm["mouth"]
    d.ellipse([mx - 8, my - 2, mx + 8, my + 12],
              fill=MOUTH_INSIDE, outline=DARK_BROWN, width=3)
    out = Image.alpha_composite(out, layer)
    return out


def draw_speak_half(base: Image.Image, lm: dict) -> Image.Image:
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    mx, my = lm["mouth"]
    d.ellipse([mx - 6, my + 1, mx + 6, my + 8],
              fill=MOUTH_INSIDE, outline=DARK_BROWN, width=3)
    out = Image.alpha_composite(out, layer)
    return out


def draw_speak_closed(base: Image.Image, lm: dict) -> Image.Image:
    # 基本就是 idle，嘴画一条平静的小线
    out = base.copy()
    layer = new_layer(out.size)
    d = ImageDraw.Draw(layer)

    mx, my = lm["mouth"]
    d.line([(mx - 5, my + 2), (mx + 5, my + 2)], fill=DARK_BROWN, width=3)
    out = Image.alpha_composite(out, layer)
    return out


def save_frame(img: Image.Image, anim: str, idx: int = 0):
    seq_dir = FRAMES_DIR / anim
    seq_dir.mkdir(parents=True, exist_ok=True)
    path = seq_dir / f"frame_{idx:02d}.png"
    img.save(path)
    print(f"  saved {path}")


def generate_all():
    print(f"Loading idle frame: {IDLE_FRAME}")
    base = load_idle()
    lm = landmarks(base)
    print(f"Landmarks: left_eye={lm['left_eye']}, right_eye={lm['right_eye']}, "
          f"mouth={lm['mouth']}, eye_dist={lm['eye_dist']}")

    configs = [
        ("angry", draw_angry),
        ("surprise", draw_surprise),
        ("sleep", draw_sleep),
        ("eat", draw_eat),
        ("speak_open", draw_speak_open),
        ("speak_half", draw_speak_half),
        ("speak_closed", draw_speak_closed),
    ]

    for anim, drawer in configs:
        print(f"Generating {anim}...")
        img = drawer(base, lm)
        save_frame(img, anim, idx=0)

    print("\nDone. 已生成 7 张情绪/口型帧。")


if __name__ == "__main__":
    generate_all()
