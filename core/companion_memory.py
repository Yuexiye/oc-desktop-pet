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

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".oc-pet" / "memory"

TOPIC_MAX_CHARS = 60          # 话题文本截断长度
HISTORY_KEEP_DAYS = 7         # 保留近 7 天摘要


class CompanionMemory:
    """桌宠长期陪伴记忆（JSON 持久化）"""

    def __init__(self, agent_id: str = "default", memory_dir: str | Path | None = None):
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
        self.load()

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
