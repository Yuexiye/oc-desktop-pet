"""avatar/motion_mixer.py — 层优先级仲裁的统一混流层（T08 · D2）

替换现有 5 道互斥补丁（emotion_motion_cooldown / _motion_is_idle /
_emote_seq_active / GESTURE_TIMEOUT 超时回 idle / b1f543c 非 idle 禁叠加），
用显式层优先级仲裁统一处理「谁占哪条通道、谁能打断谁」。

层优先级（高→低）：
    USER_INITIATED (4) — 用户触发动作（鼠标点击/拖拽/waving）
    DIALOG         (3) — 对话驱动表情（气泡对话、回复态）
    SCREEN         (2) — 屏幕感知/情绪触发动作
    IDLE           (1) — 待机微摆动

仲裁规则：
    - 高优先级可打断低优先级
    - 同优先级不打断（first-come-first-served）
    - 强制重置（force_reset）清除所有层，进入 3 秒防闪烁冷却

线程安全：非线程安全；由 Live2DRenderer 在主线程 tick 中串行调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

import time as _time


# ── 层优先级 ──

class Layer(IntEnum):
    """动作来源层。值越大优先级越高。"""
    IDLE = 1
    SCREEN = 2
    DIALOG = 3
    USER_INITIATED = 4

    @classmethod
    def from_str(cls, name: str) -> "Layer":
        mapping = {
            "idle": cls.IDLE,
            "screen": cls.SCREEN,
            "dialog": cls.DIALOG,
            "user": cls.USER_INITIATED,
            "user_initiated": cls.USER_INITIATED,
        }
        return mapping.get(name.lower(), cls.IDLE)


# ── 混流请求 ──

@dataclass
class MotionRequest:
    """一次动作/表情提交请求。"""
    layer: Layer
    motion_group: Optional[str] = None      # motion 组名（"happy"/"waving"等）
    expression_name: Optional[str] = None   # 表情名（SetExpression 用）
    params: Optional[dict] = None           # 直接参数目标（走程序化表情层）
    duration: float = 0.0                  # 0=直到被更高优先级替换；>0=自动过期
    can_interrupt: bool = True              # 同优先级是否允许打断
    name: str = ""                          # 日志用名称


# ── 当前活跃状态快照 ──

@dataclass
class ActiveState:
    """当前混流层仲裁结果（供 render 读取）。"""
    motion_group: Optional[str] = None
    expression_name: Optional[str] = None
    params: Optional[dict] = None
    layer: Layer = Layer.IDLE
    name: str = ""


# ── 混流器 ──

class MotionMixer:
    """统一混流：层优先级仲裁，替换 5 道互斥补丁。

    设计原则：
    - 仲裁只在上层做（mixer 层）；底层不假设 wrapper 优雅
    - 强制重置（force_reset）保留为独立接口，供 _force_idle 调用
    - 3 秒防闪烁冷却在 force_reset 后生效
    """

    # 强制重置后的防闪烁冷却秒数（沿用 3be390a）
    RESET_COOLDOWN_S: float = 3.0

    def __init__(self) -> None:
        self._requests: list[MotionRequest] = []
        self._active_idx: int = -1
        self._active_since: float = 0.0
        self._last_reset_at: float = 0.0
        self._next_id: int = 0

    # ── 提交 ──

    def submit(self, req: MotionRequest) -> bool:
        """提交动作请求。返回 True 表示请求被接受（可能替换当前活跃请求）。

        仲裁规则：
        1. 防闪烁冷却期内，非 force 请求一律拒绝
        2. 高优先级可打断低优先级 → 替换
        3. 同优先级：can_interrupt=True 才替换，否则拒绝
        4. 低优先级一律拒绝
        """
        if self._in_reset_cooldown():
            return False

        now = _time.monotonic()
        if not self._has_active():
            self._requests.append(req)
            self._active_idx = len(self._requests) - 1
            self._active_since = now
            return True

        current = self._active()
        if req.layer > current.layer:
            self._active_idx = len(self._requests)
            self._requests.append(req)
            self._active_since = now
            return True

        if req.layer < current.layer:
            return False

        # 同优先级
        if req.can_interrupt and current.can_interrupt:
            self._active_idx = len(self._requests)
            self._requests.append(req)
            self._active_since = now
            return True
        return False

    # ── 查询 ──

    def get_active(self) -> Optional[ActiveState]:
        """获取当前仲裁结果。过期请求自动清理。"""
        now = _time.monotonic()
        if self._has_active():
            active = self._active()
            if active.duration > 0 and (now - self._active_since) >= active.duration:
                self._active_idx = -1
                return None
        if not self._has_active():
            return None
        active = self._active()
        return ActiveState(
            motion_group=active.motion_group,
            expression_name=active.expression_name,
            params=active.params,
            layer=active.layer,
            name=active.name,
        )

    def get_active_layer(self) -> Layer:
        """获取当前活跃层。无活跃请求时返回 IDLE。"""
        state = self.get_active()
        return state.layer if state else Layer.IDLE

    def is_idle(self) -> bool:
        """是否处于 idle 层（无高优先级动作在播）。"""
        return self.get_active_layer() == Layer.IDLE

    def active_name(self) -> str:
        """当前活跃请求的名称（日志用）。"""
        state = self.get_active()
        if state is None:
            return "idle"
        return state.name or state.motion_group or "idle"

    # ── 强制重置 ──

    def force_reset(self) -> None:
        """强制重置：清除所有层，进入 3 秒防闪烁冷却。

        对应 Live2DRenderer._force_idle() 的仲裁层调用。
        调用后任何 submit() 都会被拒绝直到冷却结束。
        """
        self._requests.clear()
        self._active_idx = -1
        self._active_since = 0.0
        self._last_reset_at = _time.monotonic()

    def is_in_reset_cooldown(self) -> bool:
        """是否在强制重置后的防闪烁冷却期内。"""
        return self._in_reset_cooldown()

    # ── 内部 ──

    def _has_active(self) -> bool:
        return self._active_idx >= 0 and self._active_idx < len(self._requests)

    def _active(self) -> MotionRequest:
        return self._requests[self._active_idx]

    def _in_reset_cooldown(self) -> bool:
        if self._last_reset_at == 0.0:
            return False
        return (_time.monotonic() - self._last_reset_at) < self.RESET_COOLDOWN_S