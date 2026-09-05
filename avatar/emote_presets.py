"""avatar/emote_presets.py — 统一 emote 预设定义（T09）

Live2D 用参数驱动（set/clear/blink 步骤），sprite 用帧序列映射。
两种渲染器共享预设名称，各自实现播放逻辑。

预设步骤类型：
  - "set"   : 设置参数目标值，持续 duration 秒
  - "clear" : 清除参数（恢复情绪目标），持续 duration 秒
  - "blink" : 眨眼脉冲（快速 eye_open 0→1→0）

Sprite 映射：每个预设对应一个动画序列名（或 None 表示无对应帧）。
"""
from __future__ import annotations

from typing import Optional

# ── Live2D 参数预设 ──

LIVE2D_PRESETS: dict[str, list[dict]] = {
    # ── 眨眼系 ──
    "blink3": [
        {"type": "blink", "times": 3, "interval": 0.7},
    ],
    "wink": [
        {"type": "set", "duration": 0.5, "params": {"eye_smile": 0.25, "mouth_form": 0.2}},
        {"type": "blink", "times": 1, "interval": 0.6, "side": "right"},
        {"type": "clear", "duration": 0.4},
    ],
    "blink_slow": [
        {"type": "blink", "times": 2, "interval": 1.2},
    ],
    # ── 脸红系 ──
    "blush": [
        {"type": "set", "duration": 2.0, "params": {"blush": 0.6, "eye_smile": 0.3, "eye_open": 0.75}},
        {"type": "clear", "duration": 0.6},
    ],
    "blush_shy": [
        {"type": "set", "duration": 1.6, "params": {"blush": 0.7, "eye_smile": 0.35, "eye_open": 0.7, "eye_ball_y": -0.16, "head_angle_y": -0.25, "mouth_form": 0.15}},
        {"type": "clear", "duration": 0.6},
    ],
    "blush_deny": [
        {"type": "set", "duration": 1.2, "params": {"blush": 0.6, "eye_smile": 0.2, "head_angle_x": -0.3}},
        {"type": "set", "duration": 0.5, "params": {"blush": 0.6, "eye_smile": 0.2, "head_angle_x": 0.3}},
        {"type": "set", "duration": 0.5, "params": {"blush": 0.6, "eye_smile": 0.2, "head_angle_x": -0.2}},
        {"type": "clear", "duration": 0.5},
    ],
    # ── 视线系 ──
    "gaze_shift": [
        {"type": "set", "duration": 1.0, "params": {"eye_ball_x": 0.22, "eye_ball_y": 0.12}},
        {"type": "set", "duration": 1.0, "params": {"eye_ball_x": -0.2, "eye_ball_y": -0.06}},
        {"type": "set", "duration": 1.0, "params": {"eye_ball_x": 0.04, "eye_ball_y": 0.0}},
        {"type": "clear", "duration": 0.3},
    ],
    "gaze_up": [
        {"type": "set", "duration": 1.2, "params": {"eye_ball_y": 0.2, "head_angle_y": 0.15, "brow_angle": 0.15}},
        {"type": "clear", "duration": 0.5},
    ],
    "gaze_down": [
        {"type": "set", "duration": 1.2, "params": {"eye_ball_y": -0.22, "head_angle_y": -0.2}},
        {"type": "clear", "duration": 0.5},
    ],
    "gaze_side": [
        {"type": "set", "duration": 1.0, "params": {"eye_ball_x": 0.28, "head_angle_x": 0.15}},
        {"type": "clear", "duration": 0.5},
    ],
    "sneak_peek": [
        {"type": "set", "duration": 0.7, "params": {"eye_ball_x": 0.3, "eye_open": 0.7, "head_angle_x": 0.1}},
        {"type": "set", "duration": 0.8, "params": {"eye_ball_x": 0.02, "eye_open": 0.85}},
        {"type": "clear", "duration": 0.3},
    ],
    # ── 微表情 ──
    "smile_soft": [
        {"type": "set", "duration": 1.8, "params": {"eye_smile": 0.45, "mouth_form": 0.3, "brow_angle": 0.15, "brow_form": 0.15}},
        {"type": "clear", "duration": 0.5},
    ],
    "smile_bright": [
        {"type": "set", "duration": 1.6, "params": {"eye_smile": 0.75, "eye_open": 0.9, "mouth_form": 0.5, "mouth_open": 0.12, "brow_angle": 0.3}},
        {"type": "clear", "duration": 0.5},
    ],
    "pout": [
        {"type": "set", "duration": 1.6, "params": {"mouth_form": -0.5, "brow_form": -0.2, "eye_ball_y": 0.08, "eye_open": 0.8}},
        {"type": "clear", "duration": 0.5},
    ],
    "angry_glare": [
        {"type": "set", "duration": 1.4, "params": {"brow_angle": -0.7, "brow_form": -0.5, "eye_smile": -0.35, "eye_open": 0.72, "mouth_form": -0.3}},
        {"type": "clear", "duration": 0.5},
    ],
    "sad_droop": [
        {"type": "set", "duration": 1.6, "params": {"brow_angle": -0.5, "brow_form": -0.45, "eye_smile": -0.3, "eye_open": 0.7, "eye_ball_y": -0.12, "mouth_form": -0.4}},
        {"type": "clear", "duration": 0.5},
    ],
    "surprise_gasp": [
        {"type": "set", "duration": 1.2, "params": {"eye_open": 1.1, "brow_angle": 0.6, "brow_form": 0.45, "mouth_open": 0.6, "eye_ball_y": 0.12, "breath_amp": 1.3, "breath_rate": 1.5}},
        {"type": "clear", "duration": 0.6},
    ],
    "think_look": [
        {"type": "set", "duration": 1.8, "params": {"eye_ball_x": 0.16, "eye_ball_y": 0.16, "head_angle_x": 0.1, "mouth_form": -0.18, "brow_angle": 0.2}},
        {"type": "clear", "duration": 0.5},
    ],
    "doubt": [
        {"type": "set", "duration": 1.6, "params": {"head_angle_x": 0.38, "brow_angle": 0.22, "brow_form": 0.15, "eye_ball_x": -0.12, "mouth_form": -0.1}},
        {"type": "clear", "duration": 0.5},
    ],
    "sigh": [
        {"type": "set", "duration": 1.6, "params": {"breath_amp": 1.7, "breath_rate": 0.55, "mouth_open": 0.18, "eye_ball_y": -0.06, "eye_open": 0.75}},
        {"type": "clear", "duration": 0.6},
    ],
    "yawn": [
        {"type": "set", "duration": 0.6, "params": {"eye_open": 0.3, "mouth_open": 0.35, "brow_form": -0.15}},
        {"type": "set", "duration": 1.2, "params": {"eye_open": 0.15, "mouth_open": 0.7, "brow_form": -0.2, "eye_smile": -0.1}},
        {"type": "clear", "duration": 0.8},
    ],
    "excited": [
        {"type": "set", "duration": 0.8, "params": {"eye_open": 1.05, "eye_smile": 0.4, "mouth_form": 0.5, "mouth_open": 0.15, "breath_amp": 1.35, "breath_rate": 1.6}},
        {"type": "set", "duration": 0.9, "params": {"eye_smile": 0.8, "eye_open": 0.85, "mouth_form": 0.55, "brow_angle": 0.3}},
        {"type": "clear", "duration": 0.5},
    ],
    "grin": [
        {"type": "set", "duration": 1.6, "params": {"mouth_form": 0.5, "brow_angle": 0.3, "eye_smile": -0.12, "head_angle_x": 0.12, "eye_open": 0.85}},
        {"type": "clear", "duration": 0.5},
    ],
    "shy": [
        {"type": "set", "duration": 1.4, "params": {"blush": 0.65, "eye_open": 0.62, "eye_ball_y": -0.12, "head_angle_y": -0.3, "eye_smile": 0.2}},
        {"type": "clear", "duration": 0.5},
    ],
    "proud": [
        {"type": "set", "duration": 1.6, "params": {"brow_angle": 0.5, "brow_form": 0.3, "eye_smile": 0.3, "mouth_form": 0.3, "head_angle_y": 0.22, "eye_ball_y": 0.08}},
        {"type": "clear", "duration": 0.5},
    ],
    "sleepy": [
        {"type": "set", "duration": 2.0, "params": {"eye_open": 0.4, "eye_ball_y": -0.06, "brow_form": -0.1, "breath_amp": 0.85, "breath_rate": 0.7}},
        {"type": "blink", "times": 1, "interval": 0.9},
        {"type": "clear", "duration": 0.6},
    ],
    # ── 头部 ──
    "nod": [
        {"type": "set", "duration": 0.5, "params": {"head_angle_y": -0.35, "eye_ball_y": -0.08}},
        {"type": "set", "duration": 0.4, "params": {"head_angle_y": 0.15, "eye_ball_y": 0.02}},
        {"type": "set", "duration": 0.5, "params": {"head_angle_y": -0.3, "eye_ball_y": -0.06}},
        {"type": "set", "duration": 0.4, "params": {"head_angle_y": 0.1}},
        {"type": "clear", "duration": 0.5},
    ],
    "head_shake": [
        {"type": "set", "duration": 0.4, "params": {"head_angle_x": -0.4, "eye_ball_x": -0.1}},
        {"type": "set", "duration": 0.4, "params": {"head_angle_x": 0.4, "eye_ball_x": 0.1}},
        {"type": "set", "duration": 0.4, "params": {"head_angle_x": -0.35, "eye_ball_x": -0.08}},
        {"type": "set", "duration": 0.4, "params": {"head_angle_x": 0.3, "eye_ball_x": 0.06}},
        {"type": "clear", "duration": 0.5},
    ],
    "head_tilt": [
        {"type": "set", "duration": 1.0, "params": {"head_angle_x": 0.42, "eye_smile": 0.2, "eye_ball_x": -0.08}},
        {"type": "clear", "duration": 0.5},
    ],
    # ── 组合 ──
    "giggle": [
        {"type": "set", "duration": 1.4, "params": {"eye_smile": 0.7, "eye_open": 0.62, "mouth_form": 0.45, "breath_amp": 1.3, "breath_rate": 1.7, "head_angle_x": 0.12}},
        {"type": "set", "duration": 0.4, "params": {"eye_smile": 0.7, "eye_open": 0.62, "mouth_form": 0.45, "breath_amp": 1.3, "breath_rate": 1.7, "head_angle_x": -0.12}},
        {"type": "clear", "duration": 0.5},
    ],
    "sneeze": [
        {"type": "set", "duration": 0.35, "params": {"eye_open": 0.08, "brow_form": -0.35, "mouth_open": 0.25}},
        {"type": "set", "duration": 0.5, "params": {"eye_open": 0.05, "brow_form": -0.4, "mouth_open": 0.65, "head_angle_y": -0.35, "breath_amp": 1.2}},
        {"type": "clear", "duration": 0.8},
    ],
    "stretch_yawn": [
        {"type": "set", "duration": 1.8, "params": {"breath_amp": 1.9, "breath_rate": 0.45, "mouth_open": 0.55, "eye_open": 0.28, "eye_smile": -0.1, "head_angle_y": 0.2}},
        {"type": "set", "duration": 0.8, "params": {"mouth_open": 0.2, "eye_open": 0.5, "breath_amp": 1.0, "breath_rate": 1.0}},
        {"type": "clear", "duration": 0.8},
    ],
}

# ── Sprite 帧映射 ──
# 每个预设对应一个动画序列名（pet.json 中的 anim 键）。
# None 表示该角色无对应帧（调用方应回退到情绪动画或跳过）。
# 角色可在 pet.json 的 "emote_map" 中覆盖默认映射。

SPRITE_PRESET_MAP: dict[str, Optional[str]] = {
    # 眨眼系
    "blink3": None,       # 精灵图通常无眨眼帧
    "wink": "wink",
    "blink_slow": None,
    # 脸红系
    "blush": "blush",
    "blush_shy": "shy",
    "blush_deny": "deny",
    # 视线系
    "gaze_shift": None,
    "gaze_up": "gaze_up",
    "gaze_down": "gaze_down",
    "gaze_side": "gaze_side",
    "sneak_peek": "sneak_peek",
    # 微表情
    "smile_soft": "smile",
    "smile_bright": "happy",
    "pout": "pout",
    "angry_glare": "angry",
    "sad_droop": "sad",
    "surprise_gasp": "surprise",
    "think_look": "think",
    "doubt": "think",
    "sigh": "sigh",
    "yawn": "yawn",
    "excited": "excited",
    "grin": "grin",
    "shy": "shy",
    "proud": "proud",
    "sleepy": "sleep",
    # 头部
    "nod": "nod",
    "head_shake": "head_shake",
    "head_tilt": "head_tilt",
    # 组合
    "giggle": "giggle",
    "sneeze": "sneeze",
    "stretch_yawn": "stretch",
}


def get_live2d_preset(name: str) -> list[dict] | None:
    """获取 Live2D 预设步骤列表。"""
    return LIVE2D_PRESETS.get(name)


def get_sprite_anim(name: str, character_map: dict | None = None) -> str | None:
    """获取 sprite 预设对应的动画名。

    Args:
        name: 预设名（如 "blush", "wink"）
        character_map: 角色级覆盖映射（pet.json 的 emote_map），None 用默认
    """
    if character_map and name in character_map:
        return character_map[name]
    return SPRITE_PRESET_MAP.get(name)


def get_preset_names() -> list[str]:
    """获取所有预设名（Live2D 和 sprite 共用）。"""
    return list(LIVE2D_PRESETS.keys())