"""交互音效 — 程序化生成，无需任何外部素材

复用 ui.gacha_sound 的成熟范式：用标准库 wave 合成短提示音，经 QSoundEffect
异步播放，全程 try/except 兜底（无音频设备 / 无多媒体后端时不崩、静默跳过）。

覆盖桌宠交互的"触感"反馈：
    pickup   拾起（拖拽开始）
    drop     落地（拖拽释放且未弹跳）
    bounce   弹跳（拖拽甩出）
    pet      抚摸（按住撸）
    pat      摸头（双击）
    happy    开心行为（摇摇 / 找到你）
    surprise 惊讶反应

调用方只需 `sfx_play("pet")`，无需关心音频后端是否存在。
"""
from __future__ import annotations

import logging
import math
import os
import struct
import tempfile
import wave

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_CACHE: dict[str, str] = {}        # name -> wav 路径
_FX_CACHE: dict[str, object] = {}  # name -> QSoundEffect 实例（避免被 GC）

# 每个音效 = 若干音符: (频率Hz, 起始秒, 时长秒, 波形, [滑音目标频率Hz])
# 波形: sine / triangle / square / noise
_SFX: dict[str, list] = {
    "pickup":  [(660, 0.0, 0.07, "sine", 880)],                 # 轻上行拾起
    "drop":    [(320, 0.0, 0.10, "sine", 150),                  # 下行落地
                (170, 0.05, 0.10, "sine", 90)],                 # + 轻"咚"
    "bounce":  [(520, 0.0, 0.06, "triangle", 720),             # 俏皮弹跳
                (760, 0.06, 0.10, "triangle", 1040)],
    "pet":     [(440, 0.0, 0.09, "sine"),                       # 柔和双音抚摸
                (660, 0.08, 0.11, "sine")],
    "pat":     [(900, 0.0, 0.07, "sine", 1200)],                # 摸头亮音
    "happy":   [(523.25, 0.0, 0.10, "sine"),                   # 上行三音 C-E-G
                (659.25, 0.10, 0.10, "sine"),
                (783.99, 0.20, 0.14, "sine")],
    "surprise":[(1046.50, 0.0, 0.09, "sine", 1320.0)],          # 高音短促
}


def _master_volume() -> float:
    """读取 config.json 中 sfx.volume 母线音量；失败返回 0.5。"""
    try:
        from config import load_config
        cfg = load_config()
        if not cfg.get("sfx", {}).get("enabled", True):
            return 0.0
        return float(cfg.get("sfx", {}).get("volume", 0.5))
    except Exception:
        return 0.5


def _synth_wav(path: str, notes: list) -> None:
    """合成多个音符到单个 wav（单声道 16-bit）。"""
    total = max(n[1] + n[2] for n in notes) + 0.05
    n = int(_SAMPLE_RATE * total)
    buf = [0.0] * n
    for note in notes:
        freq, start, dur = note[0], note[1], note[2]
        wave_kind = note[3] if len(note) > 3 else "sine"
        slide_to = note[4] if len(note) > 4 else None
        s0 = int(_SAMPLE_RATE * start)
        s1 = int(_SAMPLE_RATE * (start + dur))
        for i in range(s0, min(s1, n)):
            t = (i - s0) / _SAMPLE_RATE
            f = freq + (slide_to - freq) * (t / max(dur, 1e-3)) if slide_to else freq
            # 指数衰减包络（避免爆音）+ 极短 attack（避免咔哒）
            env = math.exp(-3.0 * t / max(dur, 1e-3))
            attack = min(1.0, t / 0.008)
            amp = 0.5 * attack * env
            if wave_kind == "sine":
                s = math.sin(2 * math.pi * f * t)
            elif wave_kind == "triangle":
                s = 2.0 * abs(2.0 * ((f * t) % 1.0) - 1.0) - 1.0
            elif wave_kind == "square":
                s = 1.0 if (f * t) % 1.0 < 0.5 else -1.0
            elif wave_kind == "noise":
                s = (hash(i) % 2000 / 1000.0 - 1.0)
            else:
                s = math.sin(2 * math.pi * f * t)
            buf[i] += amp * s
    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    scale = 0.85 * 32767 / peak
    frames = struct.pack("<" + "h" * n, *[int(x * scale) for x in buf])
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(frames)


def _ensure_wav(name: str) -> str | None:
    if name in _CACHE:
        return _CACHE[name]
    notes = _SFX.get(name)
    if not notes:
        return None
    fname = f"oc_pet_sfx_{name}.wav"
    path = os.path.join(tempfile.gettempdir(), fname)
    try:
        if not os.path.exists(path):
            _synth_wav(path, notes)
        _CACHE[name] = path
        return path
    except Exception:
        logger.exception("生成交互音效失败: %s", name)
        return None


def play(name: str, volume: float | None = None, gain: float = 1.0) -> None:
    """播放交互音效（无副作用：失败静默跳过）。

    Args:
        name: 音效名（见 _SFX）
        volume: 音量 0.0~1.0；None 时使用 config 中的 sfx.volume 母线
        gain: 单次相对响度（最终音量 = volume * gain），用于不同交互的响度层级
    """
    try:
        from PySide6.QtMultimedia import QSoundEffect
        from PySide6.QtCore import QUrl
        if name not in _SFX:
            return
        path = _ensure_wav(name)
        if not path:
            return
        fx = _FX_CACHE.get(name)
        if fx is None:
            fx = QSoundEffect()
            fx.setSource(QUrl.fromLocalFile(path))
            _FX_CACHE[name] = fx
        master = _master_volume() if volume is None else volume
        final = max(0.0, min(1.0, master * gain))
        if final <= 0.0:
            return
        fx.setVolume(final)
        fx.play()
    except Exception:
        logger.exception("播放交互音效失败: %s", name)
