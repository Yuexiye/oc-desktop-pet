"""微软 Edge TTS - 免费在线合成（edge-tts 库）

使用微软 Edge 浏览器的朗读接口（无需注册、无需 API key、零成本）。
输出 mp3，秒级合成；QMediaPlayer 原生支持 mp3 无需转码。

音色：微软自带 zh-CN 系列（晓晓 XiaoxiaoNeural 等），不做角色克隆映射。
可经 config `tts.edge_voice` 覆盖默认音色（设置面板「微软 Edge」引擎下可选）。

依赖：edge-tts>=6.1.0（pip install edge-tts）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

from .base import TTSProvider

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path.home() / ".hanako" / "pets" / "tts_cache"

# 微软自带中文音色（edge-tts 服务端音色名）
EDGE_VOICES = [
    "zh-CN-XiaoxiaoNeural",   # 晓晓（女，温暖，默认）
    "zh-CN-XiaoyiNeural",     # 晓伊（女，活泼）
    "zh-CN-YunxiNeural",      # 云希（男，少年）
    "zh-CN-YunjianNeural",    # 云健（男，成熟）
    "zh-CN-YunyangNeural",    # 云扬（男，新闻播报）
    "zh-CN-XiaochenNeural",   # 晓辰（女，儿童）
    "zh-CN-XiaohanNeural",    # 晓涵（女，柔和）
    "zh-CN-XiaomengNeural",   # 晓梦（女，俏皮）
    "zh-CN-XiaomoNeural",     # 晓墨（女，叙事）
    "zh-CN-XiaoruiNeural",    # 晓睿（女，少儿）
    "zh-CN-XiaoshuangNeural", # 晓双（女，儿童）
    "zh-CN-XiaoxuanNeural",   # 晓萱（女，甜）
    "zh-CN-XiaoyanNeural",    # 晓颜（女，童声）
    "zh-CN-XiaoyouNeural",    # 晓悠（女，幼童）
    "zh-CN-XiaozhenNeural",   # 晓臻（女，温和）
    "zh-CN-YunfengNeural",    # 云枫（男，成熟）
    "zh-CN-YunhaoNeural",     # 云皓（男，磁性）
    "zh-CN-YunxiaNeural",     # 云夏（男，少年）
    "zh-CN-YunyeNeural",      # 云野（男，元气）
    "zh-CN-YunzeNeural",      # 云泽（男，浑厚）
    "zh-CN-liaoning-XiaobeiNeural",  # 晓北（东北话女）
    "zh-CN-shaanxi-XiaoniNeural",    # 晓妮（陕西话女）
]

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class EdgeTtsProvider(TTSProvider):
    """微软 Edge TTS provider（免费在线，edge-tts 库）"""

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+0%", pitch: str = "+0Hz"):
        self._voice = voice or DEFAULT_VOICE
        self._rate = rate
        self._pitch = pitch
        self._ready = False
        self._last_error = ""

    def configure(self, voice: str = "", rate: str = "", pitch: str = ""):
        """从配置覆盖默认参数"""
        if voice:
            self._voice = voice
        if rate:
            self._rate = rate
        if pitch:
            self._pitch = pitch

    @property
    def name(self) -> str:
        return "edge"

    @property
    def is_ready(self) -> bool:
        return self._ready

    def preload(self):
        """轻量预检：库可用即就绪（在线服务，网络失败在 synthesize 期暴露）"""
        try:
            import edge_tts  # noqa: F401
            self._ready = True
            logger.info("Edge TTS ready | voice=%s | rate=%s | pitch=%s",
                        self._voice, self._rate, self._pitch)
        except ImportError:
            self._ready = False
            self._last_error = "edge-tts 未安装：pip install edge-tts"
            logger.warning("Edge TTS preload 失败: %s", self._last_error)

    def synthesize(self, text: str, character_id: str = "", instruct: str = "") -> Optional[str]:
        if not text or not text.strip():
            return None
        text = text.strip()[:500]
        try:
            import edge_tts
        except ImportError as e:
            self._last_error = f"edge-tts 未安装: {e}"
            logger.warning("Edge TTS: %s", self._last_error)
            return None

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 缓存：同文本+音色+语速复用（instruct 情感不参与，edge 不支持精细情感）
        cache_key = f"edge:{self._voice}:{self._rate}:{text}"
        text_hash = hashlib.md5(cache_key.encode()).hexdigest()[:12]
        output_path = OUTPUT_DIR / f"edge_{text_hash}.mp3"

        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info("Edge TTS cache hit: %s", output_path.name)
            return str(output_path)

        try:
            # 异步接口：放入新事件循环执行（worker 线程无 Qt 循环，asyncio.run 安全）
            async def _synth():
                comm = edge_tts.Communicate(text, self._voice, rate=self._rate, pitch=self._pitch)
                await comm.save(str(output_path))

            asyncio.run(_synth())
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info("Edge TTS done: %s (%d bytes)",
                            output_path.name, output_path.stat().st_size)
                self._ready = True
                return str(output_path)
            logger.warning("Edge TTS: 合成结果为空文件")
            return None
        except Exception as e:
            self._last_error = str(e)
            logger.warning("Edge TTS 合成失败: %s", e)
            return None

    def get_speaker_info(self, character_id: str) -> dict:
        return {"voice": self._voice, "provider": "edge-tts", "free": True}