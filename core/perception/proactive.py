"""主动对话调度 — 规则引擎 + 空闲检测 + 前台分类 + 活动感知

触发流程：
1. tick() 检查是否过冷却期
2. 计算对话空闲时间（since last conversation）
3. 按 idle_min 倒序遍历 rules，匹配 foreground 分类
4. 检查活动状态（typing/idle/mouse）——**打字中打断成本高，降低触发权重**
5. 命中规则的 weight 概率触发，并通过 on_proactive 回调上抛 prompt

打扰成本逻辑：
    typing → 用户专注打字，打断成本最高，权重乘以 COST_TYPING（默认 0.2）
    mouse  → 用户在操作鼠标，可能划水也可能工作，权重乘以 COST_MOUSE（0.6）
    idle   → 用户不在/长时间空闲，最佳时机，权重不变

外部依赖：
- random（概率触发）
- motion.activity_tracker（可选注入，未注入时退回纯规则）
"""
from __future__ import annotations

import logging
import random
import time

logger = logging.getLogger(__name__)


DEFAULT_RULES = [
    {"idle_min": 5,  "foreground": ["writing", "development", "browsing"], "prompt": "写了这么久，休息一下吧？", "weight": 0.7},
    {"idle_min": 15, "foreground": ["gaming", "entertainment"],             "prompt": "带我一起玩嘛～",          "weight": 0.5},
    {"idle_min": 30, "foreground": ["communication"],                       "prompt": "还在忙吗？想和你说说话～", "weight": 0.3},
    {"idle_min": 60, "foreground": ["*"],                                    "prompt": "好安静啊……你在做什么呢？",  "weight": 0.3},
]

# 打扰成本乘数（活动状态 → 权重衰减）
COST_TYPING = 0.2    # 打字中：打断成本最高
COST_MOUSE = 0.6     # 鼠标活动：中断成本中等
COST_IDLE = 1.0      # 空闲/离开：最佳时机，权重不变

# 打字中完全抑制的时间阈值：持续打字超过该时长则打死不搭话
TYPING_SUPPRESS_SECONDS = 60.0


class ProactiveScheduler:
    """主动对话调度器 - 规则引擎 + 空闲检测 + 前台分类 + 活动感知"""

    def __init__(self, foreground_watcher=None, on_proactive: callable = None, activity_tracker=None):
        self._foreground_watcher = foreground_watcher
        self._activity_tracker = activity_tracker  # 可选注入 ActivityTracker
        self._enabled = True
        self._cooldown_minutes = 10
        self._rules: list[dict] = list(DEFAULT_RULES)
        self._cooldown_until: float = 0.0
        self._last_conversation: float = time.time()  # 上次对话时间
        self._typing_since: float | None = None  # 连续打字起始时间（None=不在打字）
        self.on_proactive: callable = on_proactive or (lambda text: None)

    def load_config(self, config: dict):
        self._enabled = config.get("enabled", True)
        self._cooldown_minutes = config.get("cooldown_minutes", 10)
        self._rules = config.get("rules", list(DEFAULT_RULES))

    def mark_conversation(self):
        """标记用户刚和桌宠对话过"""
        self._last_conversation = time.time()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def reset(self):
        self._cooldown_until = time.time() + self._cooldown_minutes * 60

    def tick(self) -> str | None:
        if not self._enabled or not self._rules:
            return None
        now = time.time()
        if now < self._cooldown_until:
            return None

        # 对话空闲时间（上次对话到现在）
        conversation_idle = now - self._last_conversation

        category = "other"
        if self._foreground_watcher:
            category = self._foreground_watcher.last_category or "other"

        # ── 活动感知：打扰成本 ──
        activity = "idle"
        cost = COST_IDLE
        if self._activity_tracker is not None:
            try:
                astate = self._activity_tracker.state
                activity = astate.state
                if activity == "typing":
                    cost = COST_TYPING
                    if self._typing_since is None:
                        self._typing_since = now
                    # 持续打字超阈值：直接抑制搭话
                    if now - self._typing_since >= TYPING_SUPPRESS_SECONDS:
                        logger.debug("Proactive suppressed: typing %ds", int(now - self._typing_since))
                        return None
                else:
                    self._typing_since = None
                    if activity == "mouse":
                        cost = COST_MOUSE
            except Exception:
                cost = COST_IDLE
        else:
            self._typing_since = None

        sorted_rules = sorted(self._rules, key=lambda r: r.get("idle_min", 0), reverse=True)
        for rule in sorted_rules:
            required_idle = rule.get("idle_min", 0) * 60
            if conversation_idle < required_idle:
                continue
            fg_match = rule.get("foreground", ["*"])
            if "*" in fg_match or category in fg_match:
                base_weight = rule.get("weight", 0.5)
                # 打扰成本衰减：typing 时权重 ×0.2，mouse 时 ×0.6
                weight = base_weight * cost
                if random.random() < weight:
                    prompt = rule.get("prompt", "")
                    if prompt:
                        self._cooldown_until = now + self._cooldown_minutes * 60
                        logger.info(
                            "Proactive triggered: idle=%ds fg=%s act=%s w=%.2f rule='%s'",
                            int(conversation_idle), category, activity, weight, prompt,
                        )
                        self.on_proactive(prompt)
                        return prompt
        return None
