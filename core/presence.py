"""轻存在感调度器：空闲时驱动低干扰微动作。

与 core.perception.proactive 互补：
- proactive  = 主动说话（规则 + 打扰成本），会打断用户。
- presence   = 不说话，只做动作/表情，让对方"在场"。

外部集成方式：
    scheduler = PresenceScheduler(on_presence=callback)
    scheduler.load_config(config.get("presence", {}))
    QTimer.singleShot(60_000, tick_loop)   # 挂载方每 60s 调用一次 scheduler.tick()
    # 用户交互时：scheduler.mark_interaction()

回调签名：
    on_presence(action: str, bubble: str) -> None
        action: play_anim 参数名（白名单内的帧序列名）
        bubble: 气泡文本，默认 ""；少数动作会有一句无害的轻提示
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── 白名单：动作名直接对应 characters/<id>/frames/<anim>/ 目录名 ──
# 来自 yuexinmiao 实际帧目录：idle / sleep / waiting / waving / review / surprise
_ACTION_WHITELIST = [
    "thinking",   # -> waiting
    "sleep",      # -> sleep
    "happy",      # -> waving
    "wave",       # -> waving（与 happy 共享帧，语义更偏"打招呼"）
]

# 动作名 → 实际帧序列名映射（白名单是语义名，帧目录用另一套）
_ACTION_TO_SEQ: dict[str, str] = {
    "idle":    "idle",
    "walk":    "running-right",
    "thinking": "waiting",
    "sleep":   "sleep",
    "surprised": "surprise",
    "happy":   "waving",
    "working": "review",
    "wave":    "waving",
}

# 气泡文本池：多数为空串（纯动作不打扰），少数轻度口语化
_BUBBLE_POOL: dict[str, list[str]] = {
    "thinking": ["……在想事情"],
    "sleep":    ["（打了个哈欠）"],
    "happy":    [],
    "wave":     [],
    "idle":     [],
    "walk":     [],
    "surprised": [],
    "working":  [],
}

# 默认配置（与 config.py 的 DEFAULT_CONFIG["presence"] 保持同步）
_DEFAULT_PRESENCE_CONFIG = {
    "enabled": True,
    "min_idle_minutes": 5,
    "interval_minutes": 8,
}


class PresenceScheduler:
    """轻存在感调度器：空闲时驱动低干扰微动作。

    - 不产生对话：只回调 on_presence(action, bubble_text_or_empty)
    - 对话进行中 / 用户刚交互 → 暂停计数
    - 动作池来自角色已有的动作名（play_anim 参数），白名单见 _ACTION_WHITELIST
    """

    def __init__(
        self,
        on_presence: Optional[Callable[[str, str], None]] = None,
        time_provider: Optional[Callable[[], float]] = None,
    ):
        """
        Args:
            on_presence: 回调 (action: str, bubble: str) -> None。
                          action 是 _ACTION_TO_SEQ 映射后的帧序列名。
            time_provider: 返回当前秒数浮点的函数；默认 time.time，便于测试注入假时钟。
        """
        self._on_presence = on_presence or (lambda action, bubble: None)
        self._time = time_provider or time.time

        self._enabled = True
        self._min_idle_seconds = _DEFAULT_PRESENCE_CONFIG["min_idle_minutes"] * 60
        self._interval_seconds = _DEFAULT_PRESENCE_CONFIG["interval_minutes"] * 60

        self._last_interaction: float = self._time()
        # 下一时刻可触发的绝对时间（进入"可存在感"状态后更新）
        self._next_at: float = 0.0
        # 是否在"可存在感"窗口中（>= min_idle 之后、每次触发后重置为 now+interval）
        self._armed = False

    def load_config(self, config: dict) -> None:
        """读 config["presence"]：{enabled, min_idle_minutes, interval_minutes}。

        缺省值：enabled=True, min_idle_minutes=5, interval_minutes=8。
        """
        cfg = config if config is not None else {}
        self._enabled = cfg.get("enabled", _DEFAULT_PRESENCE_CONFIG["enabled"])
        self._min_idle_seconds = cfg.get(
            "min_idle_minutes", _DEFAULT_PRESENCE_CONFIG["min_idle_minutes"]
        ) * 60
        self._interval_seconds = cfg.get(
            "interval_minutes", _DEFAULT_PRESENCE_CONFIG["interval_minutes"]
        ) * 60

    def mark_interaction(self) -> None:
        """用户有交互 / 对话时调用：重置空闲计时，撤销已发射的下一个触发点。"""
        now = self._time()
        self._last_interaction = now
        self._armed = False
        self._next_at = 0.0

    def tick(self) -> Optional[str]:
        """周期性调用（挂载方用 QTimer 每 60s 调一次；本类不引 Qt）。

        Returns:
            满足条件时返回触发用的"语义动作名"（白名单字符串），
            并在内部调用 on_presence；否则返回 None。
        """
        if not self._enabled:
            return None

        now = self._time()

        # 1. 判断是否进入"可存在感"状态
        idle_sec = now - self._last_interaction
        if idle_sec < self._min_idle_seconds:
            return None

        # 2. 若尚未进入窗口，进入并设定触发点
        if not self._armed:
            self._armed = True
            self._next_at = now  # 立即触发（本次 tick）

        # 3. 到了触发时刻？
        if now < self._next_at:
            return None

        # 触发：选动作，回调，推进下次触发时间
        self._next_at = now + self._interval_seconds
        action, bubble = self._pick_action()
        real_seq = _ACTION_TO_SEQ.get(action, "idle")
        try:
            self._on_presence(real_seq, bubble)
        except Exception:
            logger.exception("Presence callback failed")
        return action

    def _pick_action(self) -> tuple[str, str]:
        """从白名单随机选一个，附带可选轻气泡文本。"""
        action = random.choice(_ACTION_WHITELIST)
        bubble = ""
        pool = _BUBBLE_POOL.get(action)
        if pool and random.random() < 0.4:
            bubble = random.choice(pool)
        return action, bubble

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False
        self._armed = False
        self._next_at = 0.0

    def reset(self) -> None:
        """硬重置（测试用 / 配置热切换后调用）。"""
        self._armed = False
        self._next_at = 0.0
        self._last_interaction = self._time()
