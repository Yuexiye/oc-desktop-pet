"""CompanionMemory — 桌宠自己的长期记忆（P2 关系）

与 LLM 侧的 MemorySnapshot（事实/身份）不同，这是桌宠【本体】的陪伴记忆：
记住用户"常做的事"和"上次聊到哪"，让桌宠跨天能接上话——P2 验收核心：
"你昨天说的那个项目后来怎么样了？"

持久化：JSON 文件 ~/.oc-pet/memory/<agent_id>.json（与养成存档同目录，独立文件）

字段：
    last_active_date : "2026-08-15"    上次活跃日期（判断"是否新的一天"）
    today            : 今日统计（foreground 分类 → 次数）
    history          : 近 7 天每日摘要 [{date, top_categories, last_topic, minutes}]
    last_topic       : 最近一次对话话题（最后一条用户消息，截断 60 字）
    last_topic_at    : 时间戳
    total_days       : 连续陪伴天数（累计）
    streak_days      : 连续活跃天数（断档重置）

用法:
    mem = CompanionMemory("miku")
    mem.record_activity("development")
    mem.record_topic("你昨天说的那个项目后来怎么样了？")
    mem.save()

    if mem.is_new_day():
        yesterday = mem.yesterday_summary()   # 昨日摘要，用于"接上话题"
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".oc-pet" / "memory"

TOPIC_MAX_CHARS = 60          # 话题文本截断长度
HISTORY_KEEP_DAYS = 7         # 保留近 7 天摘要


class CompanionMemory:
    """桌宠长期陪伴记忆（JSON 持久化）"""

    def __init__(self, agent_id: str = "default", memory_dir: str | Path | None = None,
                 emotion_provider: Callable[[], str] | None = None):
        self._agent_id = agent_id
        self._dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self._path = self._dir / f"{agent_id}.json"
        self._today: Counter = Counter()          # 今日 foreground 分类统计
        self._history: list[dict] = []            # 近 7 天每日摘要
        self._last_active_date: str = ""
        self._last_topic: str = ""
        self._last_topic_at: float = 0.0
        self._total_days: int = 0
        self._streak_days: int = 0
        self._today_minutes: float = 0.0          # 今日累计在线分钟（近似）
        self._session_start: float = time.time()
        # ── A/B：事件流（可选注入；未注入时 record_event/read_events/prune_events 为空操作）──
        self._event_stream = None
        self._emotion_provider = emotion_provider
        self.load()

    # ── A/B：事件流注入（与旧 JSON 平级的新文件，不破坏旧字段）──

    def set_event_stream(self, event_stream) -> None:
        """注入 EventStream 实例（A 事件流写入端）。"""
        self._event_stream = event_stream

    def set_emotion_provider(self, provider: Callable[[], str] | None) -> None:
        """注入情绪快照提供者（B：事件结束时快照 emotion）。

        provider 为可调用对象，返回当前情绪字符串（如 EmotionStateMachine.current）。
        """
        self._emotion_provider = provider

    def _snapshot_emotion(self) -> tuple[str, float]:
        """取当前情绪快照（纯内存读，线程安全由 provider 保证）。

        Returns:
            (emotion, intensity)；provider 缺失/异常返回 ("neutral", 0.0)
        """
        if self._emotion_provider is None:
            return ("neutral", 0.0)
        try:
            emotion = self._emotion_provider()
            if not emotion:
                return ("neutral", 0.0)
            intensity = 0.0
            # 若 provider 返回元组 (emotion, intensity) 也兼容
            if isinstance(emotion, (tuple, list)) and len(emotion) >= 2:
                emotion, intensity = emotion[0], float(emotion[1] or 0.0)
            return (str(emotion), intensity)
        except Exception as e:
            logger.debug("情绪快照失败（用 neutral）: %s", e)
            return ("neutral", 0.0)

    # ── 加载 / 保存 ──

    def load(self) -> None:
        """从磁盘加载记忆；文件不存在/损坏则用默认空档。"""
        try:
            if not self._path.exists():
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._last_active_date = data.get("last_active_date", "")
            self._history = data.get("history", [])
            self._last_topic = data.get("last_topic", "")
            self._last_topic_at = data.get("last_topic_at", 0.0)
            self._total_days = data.get("total_days", 0)
            self._streak_days = data.get("streak_days", 0)
            today = data.get("today", {})
            self._today = Counter(today)
        except Exception as e:
            logger.warning("CompanionMemory 加载失败（用空档）: %s", e)

    def save(self) -> None:
        """写回磁盘（幂等）。"""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            data = {
                "agent_id": self._agent_id,
                "last_active_date": self._last_active_date,
                "today": dict(self._today),
                "history": self._history,
                "last_topic": self._last_topic,
                "last_topic_at": self._last_topic_at,
                "total_days": self._total_days,
                "streak_days": self._streak_days,
            }
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("CompanionMemory 保存失败: %s", e)

    # ── 日期处理 ──

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def is_new_day(self) -> bool:
        """今天是否与记录的上次活跃日期不同（跨天 / 首次启动）。"""
        return self._last_active_date != self._today_str()

    def _rollover_if_new_day(self) -> None:
        """若跨天：把今日统计归档进 history，重置今日计数，更新 streak。"""
        today = self._today_str()
        if self._last_active_date == today:
            return
        # 判断上次活跃是否恰好是"昨天"（仅隔一天 → streak 连续）
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            was_yesterday = self._last_active_date == yesterday
        except Exception:
            was_yesterday = False
        # 归档昨日（有当日统计才归档）
        if self._last_active_date and self._today:
            entry = self._yesterday_entry()
            self._history.insert(0, entry)
            self._history = self._history[:HISTORY_KEEP_DAYS]
        # streak：昨天有活跃 → 连续 +1；否则断档重置为 1
        if was_yesterday:
            self._streak_days += 1
        else:
            self._streak_days = 1
        self._total_days += 1
        self._last_active_date = today
        self._today = Counter()
        self._today_minutes = 0.0
        self._session_start = time.time()

    def _yesterday_entry(self) -> dict:
        top = self._today.most_common(3)
        return {
            "date": self._last_active_date,
            "top_categories": [{"category": c, "count": n} for c, n in top],
            "last_topic": self._last_topic,
            "minutes": int(self._today_minutes),
        }

    # ── 记录 ──

    def record_activity(self, category: str) -> None:
        """记录一次前台分类活动（由 ForegroundWatcher 回调驱动）。"""
        if not category or category == "uncategorized":
            return
        self._rollover_if_new_day()
        self._today[category] += 1

    def record_topic(self, text: str) -> None:
        """记录最近一次对话话题（用户消息摘要）。"""
        if not text:
            return
        self._rollover_if_new_day()
        cleaned = " ".join(text.split())[:TOPIC_MAX_CHARS]
        if cleaned:
            self._last_topic = cleaned
            self._last_topic_at = time.time()

    # ── A/B：事件流写入 ──

    def record_event(self, category: str = "", scenario: str = "", intent: str = "",
                     emotion: str = "", intensity: float = 0.0, topic: str = "",
                     start_ts: float = 0.0, end_ts: float = 0.0,
                     source: str = "") -> None:
        """写入一条事件到事件流（A 事件流 + B 情绪标签）。

        - emotion 为空时由 emotion_provider 自动快照（B 的落地）；
          emotion 非空但未给 intensity 时 intensity 保持传入值（默认 0.0）
        - topic 隐私截断（≤60 字，EventStream 内再兜底一次）
        - source="vision" 时只记 category/时间，不落 topic 文本（隐私，
          EventStream.normalize_record 也会再剥离一次，双保险）
        - 未注入 EventStream 时为空操作（回滚安全）

        Args:
            category: 前台分类（development/gaming/...）
            scenario: 意图分类场景名（可空）
            intent: 意图名（可空）
            emotion: 情绪名（可空，由 provider 自动填）
            intensity: 情绪强度 0.0~1.0（emotion 为空时由 provider 快照）
            topic: 最近对话话题（≤60 字，可选，隐私截断）
            start_ts: 活动开始时间
            end_ts: 活动结束时间（0 = 用当前时间）
            source: 来源标记（foreground|vision|topic）
        """
        if self._event_stream is None:
            return
        try:
            now = time.time()
            end = end_ts or now
            start = start_ts or end
            emo = emotion
            emo_intensity = intensity
            if not emo:
                emo, emo_intensity = self._snapshot_emotion()
            # 隐私：vision 事件不携带对话/视觉文本
            privacy_topic = "" if str(source or "").strip() == "vision" else (topic or "")
            self._event_stream.append({
                "ts": end,
                "start_ts": start,
                "end_ts": end,
                "category": category or "",
                "scenario": scenario or "",
                "intent": intent or "",
                "emotion": emo or "neutral",
                "intensity": emo_intensity,
                "topic": privacy_topic,
                "source": source or "",
            })
        except Exception as e:
            logger.debug("record_event 失败: %s", e)

    def read_events(self, days: int = 7) -> list[dict]:
        """读取最近 days 天的事件（委托 EventStream；未注入返回空列表）。"""
        if self._event_stream is None:
            return []
        try:
            cutoff = time.time() - float(days) * 86400.0
            return self._event_stream.read_since(cutoff)
        except Exception as e:
            logger.debug("read_events 失败: %s", e)
            return []

    def prune_events(self) -> int:
        """裁剪事件流超限（委托 EventStream.prune；未注入返回 0）。"""
        if self._event_stream is None:
            return 0
        try:
            return self._event_stream.prune()
        except Exception as e:
            logger.debug("prune_events 失败: %s", e)
            return 0

    def tick_online(self) -> None:
        """更新今日在线时长（由 tick 周期调用，近似统计）。"""
        if self.is_new_day():
            self._rollover_if_new_day()
        self._today_minutes = (time.time() - self._session_start) / 60.0

    # ── 读取摘要 ──

    def yesterday_summary(self) -> str:
        """生成"昨天你在做什么"的自然语言摘要（用于隔天接话题）。"""
        if not self._history:
            return ""
        yesterday = self._history[0]
        date = yesterday.get("date", "")
        top = yesterday.get("top_categories", [])
        topic = yesterday.get("last_topic", "")
        parts = []
        if top:
            cats = "、".join(f"{c['category']}（{c['count']}次）" for c in top[:2])
            parts.append(f"昨天主要在用{cats}")
        if topic:
            parts.append(f"聊到「{topic}」")
        if parts:
            return f"{date}：{'，'.join(parts)}"
        return ""

    def day_summary(self) -> str:
        """今日摘要（实时）。"""
        if not self._today:
            return ""
        top = self._today.most_common(3)
        cats = "、".join(f"{c}×{n}" for c, n in top)
        return f"今天在：{cats}"

    @property
    def streak(self) -> int:
        return self._streak_days

    @property
    def total_days(self) -> int:
        return self._total_days

    @property
    def last_topic(self) -> str:
        return self._last_topic

    def close(self) -> None:
        """关电脑/退出前调用：归档今日 + 保存。"""
        self._rollover_if_new_day()
        self.save()
        logger.info("CompanionMemory 已保存（agent=%s, streak=%d）", self._agent_id, self._streak_days)
