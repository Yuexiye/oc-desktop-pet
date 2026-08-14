"""本地 Whisper ASR - 从 voice_input.py 提取

懒加载 base 模型，首次调用时加载（约 1GB VRAM）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .base import ASRProvider

logger = logging.getLogger(__name__)

# 确保 ffmpeg 可用
try:
    import imageio_ffmpeg
    _ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ.setdefault("FFMPEG_BINARY", _ffmpeg)
    _ffmpeg_dir = os.path.dirname(_ffmpeg)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass


class WhisperLocalProvider(ASRProvider):
    """本地 Whisper ASR"""

    _model = None
    _loading = False
    _loaded = False
    _MODEL_SIZE = "small"  # whisper 模型尺寸：base≈中文差, small≈可用, medium≈好（更重）

    @classmethod
    def _resolve_model_size(cls) -> str:
        """从配置读取模型尺寸（asr.model），默认 small。"""
        try:
            from config import load_config
            cfg = load_config()
            return cfg.get("asr", {}).get("model", cls._MODEL_SIZE) or cls._MODEL_SIZE
        except Exception:
            return cls._MODEL_SIZE

    @property
    def name(self) -> str:
        return "whisper_local"

    @property
    def is_ready(self) -> bool:
        return WhisperLocalProvider._loaded and WhisperLocalProvider._model is not None

    def preload(self):
        if WhisperLocalProvider._loaded:
            return
        if WhisperLocalProvider._loading:
            return
        WhisperLocalProvider._loading = True
        try:
            import whisper
            size = WhisperLocalProvider._resolve_model_size()
            logger.info("Whisper 模型加载中... (%s)", size)
            WhisperLocalProvider._model = whisper.load_model(size)
            WhisperLocalProvider._loaded = True
            logger.info("Whisper 模型就绪 (%s)", size)
        except Exception as e:
            logger.error("Whisper 加载失败: %s", e)
        finally:
            WhisperLocalProvider._loading = False

    def transcribe(self, audio_path: str, language: str = "zh") -> Optional[str]:
        """识别音频文件

        Args:
            audio_path: WAV 文件路径
            language: 语言代码（空=自动，传给 whisper 时 None 触发自动检测）
        """
        # 空/未传时支持自动语言检测（Whisper 可识别 90+ 语言，不限于中文）
        if language == "auto":
            language = None
        if not WhisperLocalProvider._model:
            # 尝试复用 voice_input 已加载的全局模型
            try:
                import voice_input
                if hasattr(voice_input, '_whisper_model') and voice_input._whisper_model is not None:
                    WhisperLocalProvider._model = voice_input._whisper_model
                    WhisperLocalProvider._loaded = True
                    logger.info("Reused voice_input global Whisper model")
            except Exception as e:
                logger.debug("Could not import voice_input: %s", e)
                pass
        logger.info("WhisperLocal.transcribe: model=%s, loaded=%s, loading=%s",
                    WhisperLocalProvider._model is not None,
                    WhisperLocalProvider._loaded,
                    WhisperLocalProvider._loading)
        if not self.is_ready:
            logger.info("WhisperLocal: not ready, calling preload()")
            self.preload()
        if not WhisperLocalProvider._model:
            logger.warning("WhisperLocal: model still None after preload")
            return None
        try:
            # initial_prompt：给模型一个中文语境提示，显著改善中文识别（
            # 之前“那天到說我媽”这类错字就是 base 模型+无语境提示的通病）。
            # 自动语言模式（language=None）下不注入，避免偏置。
            _kw = {}
            if language is not None:
                _kw["initial_prompt"] = "以下是普通话的句子，请用简体中文转写。"
            result = WhisperLocalProvider._model.transcribe(
                audio_path,
                language=language,
                **_kw,
            )
            text = result.get("text", "").strip()
            logger.info("ASR result: %s", text[:50])
            return text if text else None
        except Exception as e:
            logger.error("ASR failed: %s", e)
            return None
