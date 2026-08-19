# -*- coding: utf-8 -*-
"""休息提醒升级 — 联动专注模式的连续工作计时状态机（P2-5）

需求：
- 连续工作超阈值（``config.work_reminder.after_minutes`` 默认 90）且**非深夜**
  （<22 点）时，主动搭话或气泡提醒休息；休息建议卡片（喝水/眼保健操/走动），
  确认后重置计时。
- 深夜（≥22 点）提醒**降频**：阈值 ×``late_night_multiplier``（默认 3），
  即 90 分钟阈值在深夜变成 270 分钟才提醒。
- 可选联动 TTS（由 pet 层探查 tts_provider 能力；本模块不依赖 Qt/TTS）。

设计：
- ``WorkReminderTracker`` 是纯 Python + ``threading.Lock`` 的状态机，
  无 Qt 依赖，可离屏单测。
- ``update(working, now)``：working=True 累积（按距上次更新的时间差），
  working=False 衰减（按 1:1 归还累积时间——用户停下即自然清零）。
- ``should_remind(now)``：累积 ≥ 阈值 && 冷却已过 → True。
- ``acknowledge(now)``：确认休息 → 清零累积 + 进入冷却（cooldown_minutes）。
- ``snooze(minutes)``：稍后提醒 → 只延冷却，保留累积。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── 默认配置（与 config.work_reminder 对齐）──
DEFAULT_WORK_REMINDER_CONFIG: dict = {
    "enabled": True,
    "after_minutes": 90,           # 连续工作提醒阈值（分钟）
    "late_night_hour": 22,         # 深夜起始小时（≥22 视为深夜）
    "late_night_end_hour": 6,      # 深夜结束小时（<6 也视为深夜，跨零点）
    "late_night_multiplier": 3.0,  # 深夜阈值倍数（降频）
    "cooldown_minutes": 60,        # 确认休息后的冷却（分钟）
    "snooze_minutes": 10,          # "稍后提醒"默认分钟
    "tts_enabled": False,          # 可选语音提醒（默认关，避免打扰）
}

# 工作/专注前台分类（与 perception 分类词汇对齐）
WORK_CATEGORIES: frozenset[str] = frozenset({
    "development", "writing", "browsing", "design", "finance", "study",
    "communication", "research",
})


@dataclass
class BreakSuggestion:
    """一条休息建议。"""

    id: str
    icon: str
    title: str
    desc: str

    def to_dict(self) -> dict:
        return {"id": self.id, "icon": self.icon,
                "title": self.title, "desc": self.desc}


# ── 休息建议池（轮换，避免重复）──
BREAK_SUGGESTIONS: list[BreakSuggestion] = [
    BreakSuggestion("water", "💧", "喝口水", "起来倒杯水，小口慢慢喝，眼睛也休息一下。"),
    BreakSuggestion("eye_exercise", "👀", "眼保健操", "闭眼转转眼球，看看远处 20 秒，放松睫状肌。"),
    BreakSuggestion("walk", "🚶", "起来走动", "离开座位走两分钟，活动活动肩颈和腰。"),
    BreakSuggestion("stretch", "🧘", "拉伸一下", "站起来伸个懒腰，转转头颈，舒展肩膀。"),
    BreakSuggestion("look_far", "🏞️", "远眺窗外", "看看 6 米外的地方 20 秒，让眼睛换个焦点。"),
    BreakSuggestion("breathe", "🌬️", "深呼吸", "吸气 4 秒、屏住 4 秒、呼气 4 秒，来三轮。"),
]


def is_late_night(now: Optional[float] = None,
                  late_night_hour: int = DEFAULT_WORK_REMINDER_CONFIG["late_night_hour"],
                  late_night_end_hour: int = DEFAULT_WORK_REMINDER_CONFIG["late_night_end_hour"]) -> bool:
    """是否深夜：hour >= late_night_hour 或 hour < late_night_end_hour（跨零点）。"""
    try:
        dt = datetime.fromtimestamp(float(now)) if now is not None else datetime.now()
        hour = dt.hour
    except (TypeError, ValueError, OSError):
        hour = datetime.now().hour
    return hour >= int(late_night_hour) or hour < int(late_night_end_hour)


def load_work_reminder_config() -> dict:
    """读取 ``config.work_reminder`` 段并深度合并默认值（失败回退默认）。"""
    cfg = dict(DEFAULT_WORK_REMINDER_CONFIG)
    try:
        from config import load_config  # type: ignore
        section = (load_config().get("work_reminder", None) or {})
        for key, value in section.items():
            if key in cfg and value is not None:
                cfg[key] = value
    except Exception as exc:  # pragma: no cover - 防御式回退
        logger.debug("work_reminder 配置读取失败，用默认: %s", exc)
    return cfg


class WorkReminderTracker:
    """连续工作休息提醒状态机（线程安全）。

    Args:
        settings: 覆盖配置 dict（缺省用 config.work_reminder + 默认值）。
        now: 可选当前时间（测试注入）。
    """

    def __init__(self, settings: Optional[dict] = None) -> None:
        s = dict(DEFAULT_WORK_REMINDER_CONFIG)
        if isinstance(settings, dict):
            for k, v in settings.items():
                if k in s and v is not None:
                    s[k] = v
        self._enabled = bool(s.get("enabled", True))
        self._after_seconds = max(60.0, float(s.get("after_minutes", 90)) * 60.0)
        self._late_night_hour = int(s.get("late_night_hour", 22))
        self._late_night_end_hour = int(s.get("late_night_end_hour", 6))
        self._late_night_multiplier = max(1.0, float(s.get("late_night_multiplier", 3.0)))
        self._cooldown_seconds = max(0.0, float(s.get("cooldown_minutes", 60)) * 60.0)
        self._snooze_seconds = max(60.0, float(s.get("snooze_minutes", 10)) * 60.0)
        self._tts_enabled = bool(s.get("tts_enabled", False))

        self._lock = threading.Lock()
        self._accumulated: float = 0.0
        self._last_update: Optional[float] = None
        self._cooldown_until: float = 0.0
        self._reminded: bool = False
        self._last_suggestion_id: str = ""

    # ── 只读属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tts_enabled(self) -> bool:
        return self._tts_enabled

    @property
    def after_seconds(self) -> float:
        return self._after_seconds

    def accumulated(self) -> float:
        with self._lock:
            return self._accumulated

    def is_reminded(self) -> bool:
        with self._lock:
            return self._reminded

    # ── 状态更新 ──

    def update(self, working: bool, now: Optional[float] = None) -> None:
        """推进计时：working=True 累积，False 衰减（1:1 归还）。"""
        if not self._enabled:
            return
        now = now if now is not None else _now()
        with self._lock:
            last = self._last_update
            self._last_update = now
            if last is None:
                return
            dt = max(0.0, now - last)
            if dt <= 0.0:
                return
            if working:
                # 累积上限需覆盖深夜放大阈值（否则深夜永远触发不了）
                cap = max(self._after_seconds * 2.0,
                          self._threshold_seconds(now) * 1.2)
                self._accumulated = min(cap, self._accumulated + dt)
            else:
                self._accumulated = max(0.0, self._accumulated - dt)
            # 重新工作超过阈值后，解除 reminded 标记（允许再次提醒）
            if self._accumulated < self._after_seconds:
                self._reminded = False

    def _threshold_seconds(self, now: Optional[float] = None) -> float:
        """当前阈值：深夜 ×multiplier（降频）。"""
        if is_late_night(now, self._late_night_hour, self._late_night_end_hour):
            return self._after_seconds * self._late_night_multiplier
        return self._after_seconds

    def due(self, now: Optional[float] = None) -> bool:
        """是否到了该提醒的时机（累积 ≥ 阈值 && 冷却已过 && 未提醒过本轮）。"""
        if not self._enabled:
            return False
        now = now if now is not None else _now()
        with self._lock:
            if now < self._cooldown_until:
                return False
            if self._reminded:
                return False
            threshold = self._threshold_seconds(now)
            return self._accumulated >= threshold

    def should_remind(self, now: Optional[float] = None) -> dict:
        """提醒判定（供 UI/接线层一次性取上下文）。

        Returns:
            {"due": bool, "late_night": bool, "threshold_min": float,
             "accumulated_min": float, "cooldown_left_min": float}
        """
        now = now if now is not None else _now()
        due = self.due(now)
        threshold = self._threshold_seconds(now)
        with self._lock:
            acc = self._accumulated
            cooldown_left = max(0.0, self._cooldown_until - now) / 60.0
        return {
            "due": due,
            "late_night": is_late_night(now, self._late_night_hour, self._late_night_end_hour),
            "threshold_min": round(threshold / 60.0, 1),
            "accumulated_min": round(acc / 60.0, 1),
            "cooldown_left_min": round(cooldown_left, 1),
        }

    def mark_reminded(self, now: Optional[float] = None) -> None:
        """标记本轮已提醒（防止同一次工作连续弹卡）。"""
        with self._lock:
            self._reminded = True
            self._last_update = now if now is not None else _now()

    def acknowledge(self, now: Optional[float] = None) -> None:
        """确认休息：清零累积 + 进入冷却。"""
        now = now if now is not None else _now()
        with self._lock:
            self._accumulated = 0.0
            self._reminded = False
            self._cooldown_until = now + self._cooldown_seconds
            self._last_update = now

    def snooze(self, minutes: Optional[float] = None, now: Optional[float] = None) -> None:
        """稍后提醒：延长冷却（保留累积）。"""
        minutes = minutes if minutes is not None else (self._snooze_seconds / 60.0)
        now = now if now is not None else _now()
        with self._lock:
            self._reminded = False
            self._cooldown_until = now + max(1.0, float(minutes)) * 60.0
            self._last_update = now

    def reset(self) -> None:
        """全量重置（切换角色/退出时）。"""
        with self._lock:
            self._accumulated = 0.0
            self._last_update = None
            self._cooldown_until = 0.0
            self._reminded = False

    # ── 休息建议 ──

    def pick_suggestion(self) -> BreakSuggestion:
        """轮换选一条建议（避免连续同一条）。"""
        pool = list(BREAK_SUGGESTIONS)
        with self._lock:
            last_id = self._last_suggestion_id
        if last_id:
            pool = [s for s in pool if s.id != last_id] or list(BREAK_SUGGESTIONS)
        # 无随机依赖：轮转式，从上次之后取第一条
        chosen = pool[0] if pool else BREAK_SUGGESTIONS[0]
        with self._lock:
            self._last_suggestion_id = chosen.id
        return chosen


def _now() -> float:
    import time
    return time.time()


__all__ = [
    "BREAK_SUGGESTIONS",
    "BreakSuggestion",
    "DEFAULT_WORK_REMINDER_CONFIG",
    "WORK_CATEGORIES",
    "WorkReminderTracker",
    "is_late_night",
    "load_work_reminder_config",
]
