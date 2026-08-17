"""主动对话调度 — 意图分类 + 规则引擎 + 空闲检测 + 前台分类 + 活动感知

触发流程：
1. tick() 检查是否过冷却期
2. 计算对话空闲时间（since last conversation）
3. 【P1 灵魂】先跑 IntentClassifier：综合 时间+前台+活动+持续时长 推断意图，
   命中场景（深夜加班/长时间工作/连续学习/频繁切窗…）且置信度达标 → 优先触发
4. 意图分类未命中时，退回旧规则引擎（按 idle_min 倒序匹配 foreground 分类）
5. 检查活动状态（typing/idle/mouse）——打字中打断成本高，降低触发权重

打扰成本逻辑：
    typing → 用户专注打字，打断成本最高，权重乘以 COST_TYPING（默认 0.2）
    mouse  → 用户在操作鼠标，可能划水也可能工作，权重乘以 COST_MOUSE（0.6）
    idle   → 用户不在/长时间空闲，最佳时机，权重不变

外部依赖：
- random（概率触发）
- motion.activity_tracker（可选注入，未注入时退回纯规则）
- core.perception.intent（P1 意图分类器）
- core.perception.scenarios（场景文案池）
"""
from __future__ import annotations

import ctypes
import logging
import random
import time

logger = logging.getLogger(__name__)

from .intent import classify_intent, LATE_NIGHT_WORK_MINUTES
from .scenarios import get_reaction, is_disruptive, get_recall_reaction, get_associate_reaction


DEFAULT_RULES = [
    {"idle_min": 5,  "foreground": ["writing", "development", "browsing"], "prompt": "写了这么久，休息一下吧？", "weight": 0.7},
    {"idle_min": 15, "foreground": ["gaming", "entertainment"],             "prompt": "带我一起玩嘛～",          "weight": 0.5},
    {"idle_min": 30, "foreground": ["communication"],                       "prompt": "还在忙吗？想和你说说话～", "weight": 0.3},
]
# 注意：idle_min=60 的兜底规则（"好安静啊……你在做什么呢？"）已被移除——
# P1 intent 的 chat_idle 场景（60 分钟没说话）已覆盖该时长，保留会导致重复触发同类文案。

# 打扰成本乘数（活动状态 → 权重衰减）
COST_TYPING = 0.2    # 打字中：打断成本最高
COST_MOUSE = 0.6     # 鼠标活动：中断成本中等
COST_IDLE = 1.0      # 空闲/离开：最佳时机，权重不变

# 打字中完全抑制的时间阈值：持续打字超过该时长则打死不搭话
TYPING_SUPPRESS_SECONDS = 60.0

# P1 意图触发的最低置信度（低于此值不触发，退回规则引擎）
INTENT_MIN_CONFIDENCE = 0.6

# P6 每日主动对话总量上限（load_config 可用 daily_limit 覆盖）
DAILY_LIMIT = 20

# P6 自适应冷却边界：被无视惩罚上限 / 用户回应奖励下限（分钟）
COOLDOWN_MAX_MINUTES = 60.0
COOLDOWN_MIN_MINUTES = 5.0

# D 场景回忆冷却（分钟，默认 30；config memory.recall.cooldown_minutes 可覆盖）
RECALL_COOLDOWN_MINUTES = 30.0


class ProactiveScheduler:
    """主动对话调度器 - 意图分类 + 规则引擎 + 空闲检测 + 前台分类 + 活动感知"""

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

        # ── P6 自适应冷却：动态冷却（无视→翻倍惩罚 / 回应→减半奖励）──
        self._current_cooldown: float = 10.0  # 动态冷却，初始同静态默认
        self._last_proactive_at: float = 0.0  # 上次主动触发时间
        self._user_replied_since_last: bool = True  # 上次触发后用户是否回应过（初始视为可触发）

        # ── P6 每日总量限制 ──
        self._daily_limit: int = DAILY_LIMIT
        self._daily_count: int = 0
        self._daily_date: str = ""

        # ── P6 全屏检测（游戏/视频全屏不打扰）──
        self._fullscreen_threshold: float = 0.95  # 窗口覆盖屏幕比例阈值（可配置）
        self._fullscreen_suppress: bool = True    # 全屏时是否抑制主动搭话（可配置）

        # ── D/E 场景记忆回忆（可选注入；未注入/开关关闭 → 整体失效、行为不变）──
        self._scene_memory = None                 # SceneMemory 实例（由 pet.py 注入）
        self._recall_enabled = True               # config memory.recall.enabled
        self._associate_enabled = True            # config memory.associate.enabled
        self._recall_cooldown_minutes: float = RECALL_COOLDOWN_MINUTES
        self._recall_cooldown_until: float = 0.0  # 回忆独立冷却截止

    def set_scene_memory(self, scene_memory) -> None:
        """注入 SceneMemory（D 场景回忆的检索端）。

        未注入时 _try_recall 直接 return None，现有主动对话路径零变化（兼容性）。
        """
        self._scene_memory = scene_memory

    def load_memory_config(self, memory_cfg: dict) -> None:
        """加载记忆层配置段（config["memory"]）。

        Args:
            memory_cfg: {"recall": {"enabled", "cooldown_minutes"},
                         "associate": {"enabled"}}
        """
        if not isinstance(memory_cfg, dict):
            return
        recall_cfg = memory_cfg.get("recall", {}) or {}
        self._recall_enabled = bool(recall_cfg.get("enabled", True))
        try:
            self._recall_cooldown_minutes = float(
                recall_cfg.get("cooldown_minutes", RECALL_COOLDOWN_MINUTES)
            )
        except Exception:
            self._recall_cooldown_minutes = RECALL_COOLDOWN_MINUTES
        assoc_cfg = memory_cfg.get("associate", {}) or {}
        self._associate_enabled = bool(assoc_cfg.get("enabled", True))

    def load_config(self, config: dict):
        self._enabled = config.get("enabled", True)
        self._cooldown_minutes = config.get("cooldown_minutes", 10)
        self._current_cooldown = float(config.get("cooldown_minutes", 10))
        self._rules = config.get("rules", list(DEFAULT_RULES))
        self._daily_limit = config.get("daily_limit", DAILY_LIMIT)
        # P6-2 全屏检测可配置：阈值（覆盖比例，默认 0.95）与总开关（默认开启）
        self._fullscreen_threshold = float(config.get("fullscreen_threshold", 0.95))
        self._fullscreen_suppress = bool(config.get("fullscreen_suppress", True))

    def mark_conversation(self, user_reply: bool = False):
        """标记对话发生。

        Args:
            user_reply: True=用户真实回应（奖励：冷却减半，下限 5 分钟）；
                        False=proactive 自身触发后的记录（只重置空闲计时，
                        不影响 _user_replied_since_last 与动态冷却）。
        """
        self._last_conversation = time.time()
        if user_reply:
            self._user_replied_since_last = True
            self._current_cooldown = max(COOLDOWN_MIN_MINUTES, self._current_cooldown * 0.5)

    def _record_proactive_trigger(self, now: float) -> None:
        """统一处理"主动触发成功"的簿记（P6）。

        - 用户此前没回应过（_user_replied_since_last=False）→ 被无视惩罚：冷却翻倍
        - 用户刚回应过（_user_replied_since_last=True）→ 合理触发，不翻倍
        - 记录 _last_proactive_at / 重置 _user_replied_since_last / 更新冷却截止 / 每日计数
        - 达到每日上限时 logger.info 一次（当天后续 tick 直接静默返回）
        """
        if not self._user_replied_since_last:
            # 上次触发后用户一直没回应 → 学乖：冷却翻倍（上限 60 分钟）
            self._current_cooldown = min(COOLDOWN_MAX_MINUTES, self._current_cooldown * 2.0)
        self._last_proactive_at = now
        self._user_replied_since_last = False
        self._daily_count += 1
        self._cooldown_until = now + self._current_cooldown * 60
        if self._daily_count == self._daily_limit:
            logger.info("Proactive daily limit reached (%d), no more triggers today", self._daily_count)

    def _is_fullscreen(self) -> bool:
        """检测当前前台窗口是否全屏（游戏/视频全屏不打扰）。

        Returns:
            True=前台窗口为真正全屏（覆盖 ≥fullscreen_threshold 屏幕且原点在 (0,0)）；
            False=非全屏（含最大化窗口）或检测失败。
        """
        try:
            from motion.foreground_watcher import _get_foreground_window_rect
            rect = _get_foreground_window_rect()
            if not rect:
                return False
            x, y, width, height = rect
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
            if screen_w <= 0 or screen_h <= 0:
                return False
            # 几何判据：覆盖 ≥fullscreen_threshold 屏幕（用户可在设置面板调阈值）。
            # 额外要求原点非负——Windows 最大化窗口的 rect 会因隐形边框带负坐标
            # （如 -7,-7），并非真正的全屏（游戏/视频全屏原点为 0,0）。避免把
            # 最大化 IDE/终端误判成全屏而一直不搭话。
            return (
                x >= 0 and y >= 0
                and width >= screen_w * self._fullscreen_threshold
                and height >= screen_h * self._fullscreen_threshold
            )
        except Exception as e:
            logger.debug("Fullscreen detection failed: %s", e)
            return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def reset(self):
        self._cooldown_until = time.time() + self._current_cooldown * 60

    def _collect_signals(self, now: float) -> dict:
        """收集意图分类所需信号（时间/前台/活动/持续时长）。"""
        signals = {
            "period": "other", "category": "other", "activity": "idle",
            "fg_duration_min": 0.0, "conversation_idle_min": 0.0,
            "window_switches_5min": 0, "is_weekend": False,
        }
        try:
            from .time import TimePerception
            tp = TimePerception()
            tctx = tp.get_context()
            signals["period"] = tctx.get("period", "other")
            signals["is_weekend"] = tctx.get("is_weekend", False)
        except Exception:
            pass
        if self._foreground_watcher:
            signals["category"] = getattr(self._foreground_watcher, "last_category", "") or "other"
            signals["fg_duration_min"] = getattr(self._foreground_watcher, "fg_duration_min", 0.0) or 0.0
            signals["window_switches_5min"] = getattr(self._foreground_watcher, "window_switches_5min", 0) or 0
        if self._activity_tracker is not None:
            try:
                signals["activity"] = self._activity_tracker.state.state
            except Exception:
                signals["activity"] = "idle"
        signals["conversation_idle_min"] = (now - self._last_conversation) / 60.0
        return signals

    def _try_intent(self, now: float, signals: dict) -> str | None:
        """P1 意图分类优先触发：命中场景 + 置信度达标 → 触发。"""
        try:
            intent = classify_intent(
                period=signals["period"],
                category=signals["category"],
                activity=signals["activity"],
                fg_duration_min=signals["fg_duration_min"],
                conversation_idle_min=signals["conversation_idle_min"],
                window_switches_5min=signals["window_switches_5min"],
                is_weekend=signals["is_weekend"],
            )
            scenario = intent.get("scenario", "")
            confidence = intent.get("confidence", 0.0)
            if confidence < INTENT_MIN_CONFIDENCE or not scenario:
                return None
            # 打扰成本：打字中且场景非"值得打扰" → 不触发
            activity = signals["activity"]
            if activity == "typing" and not is_disruptive(scenario):
                logger.debug("Proactive intent skipped: typing + 非值得打扰场景 %s", scenario)
                return None
            # 深夜加班类场景：即使打字也值得提醒（强度高）
            reaction = get_reaction(scenario, intensity=confidence)
            prompt = reaction["text"]
            self._record_proactive_trigger(now)
            logger.info(
                "Proactive intent triggered: scenario=%s intent=%s conf=%.2f %s",
                scenario, intent.get("intent"), confidence, intent.get("reason"),
            )
            self.on_proactive(prompt)
            return prompt
        except Exception as e:
            logger.debug("Proactive intent failed (fallback to rules): %s", e)
            return None

    def _try_recall(self, now: float, signals: dict) -> str | None:
        """D/E 场景回忆触发：命中历史场景 → 说一句带记忆的话。

        调用顺序（tick 内）：_try_intent（意图优先）→ _try_recall（回忆）→ 规则兜底。
        回忆不抢意图的触发机会（意图命中则回忆让位）。

        守卫（复用 tick 已有守卫）：
          - 未注入 scene_memory / memory.recall.enabled=False → return None
          - 独立冷却 _recall_cooldown_until（默认 30 分钟）
          - 打字中且场景非值得打扰（is_disruptive）→ return None
          - 每日上限 / 全屏 / 持续打字抑制已在 tick 统一处理

        E 联想：回忆未命中且 memory.associate.enabled → associate 标签交集联想。
        """
        if self._scene_memory is None or not self._recall_enabled:
            return None
        try:
            if now < self._recall_cooldown_until:
                return None
            category = signals.get("category", "") or ""
            if not category or category == "other":
                return None
            # 用意图分类推导场景名（回忆匹配维度；失败不阻塞）
            scenario = ""
            emotion = "neutral"
            period = signals.get("period", "other")
            try:
                intent = classify_intent(
                    period=period,
                    category=category,
                    activity=signals.get("activity", "idle"),
                    fg_duration_min=signals.get("fg_duration_min", 0.0),
                    conversation_idle_min=signals.get("conversation_idle_min", 0.0),
                    window_switches_5min=signals.get("window_switches_5min", 0),
                    is_weekend=signals.get("is_weekend", False),
                )
                scenario = intent.get("scenario", "") or ""
            except Exception:
                scenario = ""
            tags = [category, scenario, period, emotion]
            # 打扰成本：打字中且场景非"值得打扰" → 不触发
            if signals.get("activity") == "typing" and scenario and not is_disruptive(scenario):
                logger.debug("Proactive recall skipped: typing + 非值得打扰场景 %s", scenario)
                return None
            matches = self._scene_memory.find_matching(category, scenario, tags, max_results=3)
            if matches:
                scene = matches[0]
                text = get_recall_reaction(scene, {"topic": scene.topics[0] if scene.topics else ""})
                if text:
                    self._record_proactive_trigger(now)
                    self._recall_cooldown_until = now + self._recall_cooldown_minutes * 60
                    logger.info(
                        "Proactive recall triggered: scene=%s category=%s scenario=%s",
                        scene.scene_id, category, scenario,
                    )
                    self.on_proactive(text)
                    return text
                return None
            # E 联想：回忆未命中且开关开启 → 标签交集联想
            if self._associate_enabled:
                current = {
                    "category": category,
                    "scenario": scenario,
                    "emotion": emotion,
                    "period": period,
                }
                scene = self._scene_memory.associate(
                    current, self._scene_memory.recent_scenes(20)
                )
                if scene is not None:
                    text = get_associate_reaction(
                        scene, {"topic": scene.topics[0] if scene.topics else ""}
                    )
                    if text:
                        self._record_proactive_trigger(now)
                        self._recall_cooldown_until = now + self._recall_cooldown_minutes * 60
                        logger.info(
                            "Proactive associate triggered: from=%s to=%s",
                            scene.scene_id, category,
                        )
                        self.on_proactive(text)
                        return text
        except Exception as e:
            logger.debug("Proactive recall failed: %s", e)
        return None

    def tick(self) -> str | None:
        if not self._enabled or not self._rules:
            return None
        now = time.time()

        # ── P6 每日总量限制：跨天重置；达到上限当天不再触发 ──
        today = time.strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0
        if self._daily_count >= self._daily_limit:
            return None

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

        # ── P6 全屏检测：游戏/视频全屏不打扰（communication 豁免；可在设置面板关闭）──
        if self._fullscreen_suppress and category != "communication" and self._is_fullscreen():
            logger.debug("Proactive suppressed: fullscreen fg=%s", category)
            return None

        # ── P1 意图分类优先触发 ──
        signals = self._collect_signals(now)
        intent_prompt = self._try_intent(now, signals)
        if intent_prompt:
            return intent_prompt

        # ── D/E 场景回忆（意图优先，回忆让位；未注入/关闭则零行为）──
        recall_prompt = self._try_recall(now, signals)
        if recall_prompt:
            return recall_prompt

        # ── 规则引擎兜底 ──
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
                        self._record_proactive_trigger(now)
                        logger.info(
                            "Proactive triggered: idle=%ds fg=%s act=%s w=%.2f rule='%s'",
                            int(conversation_idle), category, activity, weight, prompt,
                        )
                        self.on_proactive(prompt)
                        return prompt
        return None
