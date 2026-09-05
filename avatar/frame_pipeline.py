"""avatar/frame_pipeline.py — 每帧管线化

T2-1: 把 live2d_renderer.py 的 2561 行 draw 方法改为管线结构

每个步骤是一个独立的处理器（Processor），按顺序执行。
某个处理器报错不影响其他处理器。

用法::

    pipeline = FramePipeline()
    pipeline.add(IdleLoopProcessor())
    pipeline.add(GestureTimeoutProcessor())
    pipeline.add(AutoMotionProcessor())
    pipeline.add(ExpressionTimeoutProcessor())
    pipeline.add(GazeProcessor())
    pipeline.add(MouthProcessor())
    pipeline.add(ProceduralEmotionProcessor())
    pipeline.add(IdleSwayProcessor())
    pipeline.add(DrawProcessor())
    pipeline.run(model, renderer)
"""
from __future__ import annotations

import logging
import time
from typing import Protocol, Optional

log = logging.getLogger(__name__)


class Processor(Protocol):
    """管线处理器协议"""
    def process(self, model: object, renderer: object) -> None: ...


class IdleLoopProcessor:
    """待机动作循环"""
    def process(self, model: object, renderer: object) -> None:
        try:
            if model.IsMotionFinished():
                if getattr(renderer, "_debug", False):
                    log.info("FramePipeline: idle 动作播完，重新触发")
                renderer._start_idle()
        except Exception as e:
            log.warning("IdleLoopProcessor: %s", e)


class GestureTimeoutProcessor:
    """手势超时防御"""
    def process(self, model: object, renderer: object) -> None:
        try:
            elapsed = time.monotonic() - renderer._motion_started_at
            if not renderer._motion_is_idle and elapsed > renderer.GESTURE_TIMEOUT:
                log.info(
                    "FramePipeline: 非 idle motion 超时 %.1fs/%.1fs（%s），强制回 idle",
                    elapsed, renderer.GESTURE_TIMEOUT,
                    getattr(renderer, "_current_motion_idx", "?"),
                )
                renderer._force_idle()
        except Exception as e:
            log.warning("GestureTimeoutProcessor: %s", e)


class AutoMotionProcessor:
    """自动随机动作"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._tick_auto_motion()
        except Exception as e:
            log.debug("AutoMotionProcessor: %s", e)


class ExpressionTimeoutProcessor:
    """表情超时兜底"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._expire_expression_if_stale()
        except Exception as e:
            log.warning("ExpressionTimeoutProcessor: %s", e)


class GazeProcessor:
    """视线跟随"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._update_gaze_params()
        except Exception as e:
            log.warning("GazeProcessor: %s", e)


class MouthProcessor:
    """口型同步"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._update_mouth()
        except Exception as e:
            log.warning("MouthProcessor: %s", e)


class ProceduralEmotionProcessor:
    """程序化表情"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._update_procedural_emotion()
        except Exception as e:
            log.warning("ProceduralEmotionProcessor: %s", e)


class IdleSwayProcessor:
    """待机摇摆"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._update_idle_sway()
        except Exception as e:
            log.warning("IdleSwayProcessor: %s", e)


class DrawProcessor:
    """绘制模型"""
    def process(self, model: object, renderer: object) -> None:
        try:
            renderer._draw_model(model)
        except Exception as e:
            log.error("DrawProcessor: %s", e)


class FramePipeline:
    """每帧管线"""

    def __init__(self):
        self._processors: list[Processor] = []

    def add(self, processor: Processor) -> "FramePipeline":
        """添加处理器"""
        self._processors.append(processor)
        return self

    def run(self, model: object, renderer: object) -> None:
        """执行管线"""
        for p in self._processors:
            try:
                p.process(model, renderer)
            except Exception as e:
                log.error("FramePipeline: processor %s failed: %s", type(p).__name__, e)

    @property
    def processors(self) -> list[Processor]:
        return self._processors


def create_default_pipeline() -> FramePipeline:
    """创建默认管线（与 live2d_renderer.py 的 draw 方法一致）"""
    pipeline = FramePipeline()
    pipeline.add(IdleLoopProcessor())
    pipeline.add(GestureTimeoutProcessor())
    pipeline.add(AutoMotionProcessor())
    pipeline.add(ExpressionTimeoutProcessor())
    pipeline.add(GazeProcessor())
    pipeline.add(MouthProcessor())
    pipeline.add(ProceduralEmotionProcessor())
    pipeline.add(IdleSwayProcessor())
    pipeline.add(DrawProcessor())
    return pipeline
