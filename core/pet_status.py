"""桌宠状态词表 — 6 态语义层（emotion/anim 之上的映射层）

G 的目标是回答"桌宠现在处于什么状态"：idle/working/review/waiting/failed/celebrating。
它是 emotion/anim **之上的映射层**，不推翻现有词表（EXPRESSION_MAP /
_ANIM_TO_MOTION_KW / hanako mood 全保留）。

每个状态 → 各渲染器的「anim + emotion」组合：
  - Sprite 走帧序列（celebrating 优先 celebrate 序列，无则 jumping→waving 回退）
  - Live2D 走 motion+表情（celebrating 优先 complete motion，无则 happy 回退）
  - VRM 占位降级：仅表情 + 文案，不播动画

关键约束（硬约束 3：不新增崩溃面）：
- 只通过 AvatarRenderer 统一接口 play_anim / set_emotion 下发
- 绝不 import / 触碰渲染器内部实现（Live2D C 层 / 渲染线程）
- 主线程调用，不新增线程

注意：`core/pet_state.py` 的 PetStateManager 是**养成系统**（hunger/thirst/energy），
与本模块完全无关；G 状态层不读养成属性。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 桌宠 6 态（F 只读口输出 / POST 白名单校验基础）
PET_STATES: tuple[str, ...] = ("idle", "working", "review", "waiting", "failed", "celebrating")


class PetStatusMapper:
    """桌宠状态语义层：状态 → 各渲染器指令组合 + 当前状态登记。

    用法（PetWindow 主线程）：
        mapper = PetStatusMapper()
        mapper.set_state("celebrating")
        mapper.render_for("celebrating", self._renderer)
    """

    # 状态 → SpriteRenderer (anim, emotion)
    STATE_TO_SPRITE: dict[str, tuple[str, str]] = {
        "idle":        ("idle",      "neutral"),
        "working":     ("review",    "working"),
        "review":      ("waiting",   "thinking"),
        "waiting":     ("waiting",   "neutral"),
        "failed":      ("failed",    "sad"),
        "celebrating": ("celebrate", "happy"),   # 序列不存在时回退 jumping → waving
    }

    # 状态 → Live2DRenderer (anim, emotion)；play_anim 内部按 _ANIM_TO_MOTION_KW 匹配 motion
    STATE_TO_LIVE2D: dict[str, tuple[str, str]] = {
        "idle":        ("idle",      "neutral"),
        "working":     ("working",   "neutral"),
        "review":      ("thinking",  "thinking"),
        "waiting":     ("idle",      "neutral"),
        "failed":      ("failed",    "sad"),
        "celebrating": ("complete",  "happy"),   # 模型无 complete motion 时由 play_anim 安全回退
    }

    # 状态 → VRM（占位降级：仅表情 + 文案，不播动画）
    STATE_TO_VRM: dict[str, tuple[str, str]] = {
        "idle":        ("idle", "neutral"),
        "working":     ("idle", "neutral"),
        "review":      ("idle", "thinking"),
        "waiting":     ("idle", "neutral"),
        "failed":      ("idle", "sad"),
        "celebrating": ("idle", "happy"),
    }

    def __init__(self):
        self._state: str = "idle"
        self._on_change = None

    # ── 状态登记 ──

    def set_state(self, state: str) -> None:
        """记录当前状态（可选回调 on_change）。非法状态回退 idle。"""
        if state not in PET_STATES:
            logger.debug("PetStatusMapper: 未知状态 %r 回退 idle", state)
            state = "idle"
        if state != self._state:
            self._state = state
            if self._on_change is not None:
                try:
                    self._on_change(state)
                except Exception as e:
                    logger.debug("PetStatusMapper on_change failed: %s", e)

    def current(self) -> str:
        """当前状态（F 只读口输出）。"""
        return self._state

    def set_on_change(self, callback) -> None:
        """设置状态变化回调（可选，主线程）。"""
        self._on_change = callback

    # ── 渲染分派 ──

    def render_for(self, state: str, renderer) -> None:
        """通过 AvatarRenderer 统一接口下发（主线程调用，不新增线程）。

        按鸭子类型识别渲染器（不 import 具体类，避免耦合），顺序（架构师复核锁定）：
          - 有 `_model` 属性 → Live2DRenderer（Live2D 也有 `_frames`，必须先判 `_model`）
          - `unsupported=True` → VRMRenderer 占位（vrm_renderer.py 有 unsupported=True
            且带 `_frames` 兼容占位；仅 set_emotion 降级，不播动画）
          - 有 `_frames` 属性 → SpriteRenderer（帧精灵；无 _model 且非 unsupported）
          - 其他 → 兜底：仅 set_emotion 降级，不崩

        Args:
            state: PET_STATES 之一
            renderer: AvatarRenderer 子类实例
        """
        if renderer is None:
            return
        if state not in PET_STATES:
            state = "idle"
        try:
            if hasattr(renderer, "_model"):
                # Live2DRenderer（有 _frames 也有 _model，必须先判 _model）
                anim, emo = self.STATE_TO_LIVE2D.get(state, ("idle", "neutral"))
                renderer.play_anim(anim, emotion=emo)
                renderer.set_emotion(emo)
            elif getattr(renderer, "unsupported", False):
                # VRMRenderer 占位（vrm_renderer.py 有 unsupported=True + _frames 兼容占位）
                # 仅表情降级，不播动画、不崩
                _, emo = self.STATE_TO_VRM.get(state, ("idle", "neutral"))
                renderer.set_emotion(emo)
            elif hasattr(renderer, "_frames"):
                # SpriteRenderer（帧精灵；无 _model 且非 unsupported）
                anim, emo = self.STATE_TO_SPRITE.get(state, ("idle", "neutral"))
                if state == "celebrating" and anim not in renderer._frames:
                    anim = "jumping" if "jumping" in renderer._frames else "waving"
                renderer.play_anim(anim, emotion=emo)
                renderer.set_emotion(emo)
            else:
                # 兜底：未知渲染器，只记录状态/表情，不崩
                _, emo = self.STATE_TO_VRM.get(state, ("idle", "neutral"))
                renderer.set_emotion(emo)
        except Exception as e:
            logger.debug("PetStatusMapper.render_for(%s) failed: %s", state, e)


__all__ = ["PetStatusMapper", "PET_STATES"]
