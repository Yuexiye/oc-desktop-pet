"""开箱音效 — 程序化生成，无需外部素材

用标准库 wave 合成简短提示音（普通/稀有/传说音色不同），缓存到临时文件，
经 QSound 异步播放。全程 try/except 兜底：无音频设备/无多媒体后端时不崩、静默跳过。
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
_CACHE: dict[str, str] = {}
_FX_CACHE: dict[str, object] = {}  # 缓存 QSoundEffect 实例，避免被 GC

# 不同稀有度对应的音符：(频率Hz, 起始秒, 时长秒)
_TONES: dict[str, list[tuple[float, float, float]]] = {
    "common":    [(587.33, 0.0, 0.22)],                       # D5 单音
    "uncommon":  [(659.25, 0.0, 0.24)],                       # E5 单音
    "rare":      [(523.25, 0.0, 0.30), (659.25, 0.0, 0.30),
                  (783.99, 0.0, 0.30)],                       # C-E-G 大三和弦
    "epic":      [(523.25, 0.0, 0.34), (659.25, 0.0, 0.34),
                  (783.99, 0.0, 0.34), (1046.50, 0.0, 0.34)],  # 加高八度
    "legendary": [(523.25, 0.0, 0.14), (659.25, 0.14, 0.14),
                  (783.99, 0.28, 0.14), (1046.50, 0.42, 0.22)],  # 上行琶音
}


def _synth_wav(path: str, notes: list[tuple[float, float, float]]) -> None:
    total = max(start + dur for _, start, dur in notes) + 0.05
    n = int(_SAMPLE_RATE * total)
    buf = [0.0] * n
    for freq, start, dur in notes:
        s0 = int(_SAMPLE_RATE * start)
        s1 = int(_SAMPLE_RATE * (start + dur))
        for i in range(s0, min(s1, n)):
            t = (i - s0) / _SAMPLE_RATE
            # 指数衰减包络，避免爆音
            env = math.exp(-3.0 * t / max(dur, 0.001))
            buf[i] += 0.5 * math.sin(2 * math.pi * freq * t) * env
    # 归一化到 16-bit 范围
    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    scale = 0.85 * 32767 / peak
    frames = struct.pack("<" + "h" * n, *[int(x * scale) for x in buf])
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(frames)


def _ensure_wav(rarity_value: str) -> str | None:
    if rarity_value in _CACHE:
        return _CACHE[rarity_value]
    notes = _TONES.get(rarity_value, _TONES["common"])
    fname = f"oc_pet_gacha_{rarity_value}.wav"
    path = os.path.join(tempfile.gettempdir(), fname)
    try:
        if not os.path.exists(path):
            _synth_wav(path, notes)
        _CACHE[rarity_value] = path
        return path
    except Exception:  # 生成失败（无写权限等）-> 静默跳过
        logger.exception("生成开箱音效失败: %s", rarity_value)
        return None


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


def play_reveal(rarity_value: str, volume: float | None = None) -> None:
    """播放对应稀有度的开箱音效（无副作用：失败静默）

    用 QSoundEffect（短音效专用，无需额外 AudioOutput）；实例缓存避免被 GC。
    volume 为 None 时使用 config 的 sfx.volume 母线并按原 0.55 相对响度缩放。
    """
    try:
        from PySide6.QtMultimedia import QSoundEffect
        from PySide6.QtCore import QUrl
        path = _ensure_wav(rarity_value)
        if not path:
            return
        fx = _FX_CACHE.get(rarity_value)
        if fx is None:
            fx = QSoundEffect()
            fx.setSource(QUrl.fromLocalFile(path))
            _FX_CACHE[rarity_value] = fx
        master = _master_volume() if volume is None else volume
        final = max(0.0, min(1.0, master * 0.55))
        if final <= 0.0:
            return
        fx.setVolume(final)
        fx.play()
    except Exception:
        logger.exception("播放开箱音效失败")
