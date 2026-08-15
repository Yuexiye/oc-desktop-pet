"""意图分类器 — 从四维信号推断用户"在干嘛/状态如何"

P1 灵魂阶段核心：从"看见你在干嘛"升级到"懂你在干嘛"。

信号源（均为零成本或已有）：
    - 时间感知     TimePerception    （时段/是否深夜/工作日）
    - 前台分类     ForegroundWatcher （writing/development/gaming/browsing/...）
    - 活动感知     ActivityTracker   （typing/mouse/idle/持续时长）
    - 持续时长     由调用方维护      （同一分类连续多久/对话空闲多久）

意图标签（intent）：
    work        在工作（写作/开发/浏览文档，长时间专注）
    learn       在学习（连续浏览教程/文档类内容）
    entertain   在娱乐（游戏/视频/直播）
    tense       高压（深夜仍在工作 / 持续无休）
    tired       疲惫（深夜 + 长时间 / 长时间无输入）

场景标签（scenario）→ 对应文案/反应（见 scenarios.py）：
    late_night_work    深夜还在加班
    long_work_break    长时间工作该休息
    tutorial_follow    连续看教程
    window_switch      频繁切窗口
    gaming             在玩游戏
    video_watching     在看视频
    chat_idle          长时间没说话
    morning_first      清晨首次
    weekend_play       周末娱乐
    late_night_all     深夜无所事事

用法:
    cls = IntentClassifier()
    intent = cls.classify(
        period="late_night", category="development",
        activity="typing", fg_duration_min=120,
        conversation_idle_min=45, window_switches_5min=3,
    )
    # intent == {"intent": "tense", "scenario": "late_night_work", "confidence": 0.9, "reason": "深夜加班"}
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── 分类常量 ──────────────────────────────────────────────

# 深夜时段（催睡阈值）
LATE_NIGHT_PERIODS = {"late_night", "midnight"}

# 前台分类 → 基础意图
CATEGORY_INTENT = {
    "writing": "work",
    "development": "work",
    "browsing": "learn",        # 浏览默认偏学习/查阅（结合持续时长）
    "gaming": "entertain",
    "entertainment": "entertain",
    "communication": "work",    # 通讯偏工作沟通（保守）
    "other": "work",
    "uncategorized": "work",
}

# 连续学习判定阈值（browsing 持续超过此分钟视为"在学"而非"瞎逛"）
LEARN_MINUTES = 15

# 深夜加班阈值：深夜 + 工作类 + 持续超过此分钟
LATE_NIGHT_WORK_MINUTES = 30

# 长时间工作阈值：工作类持续超过此分钟（催休息）
LONG_WORK_MINUTES = 90

# 疲惫判定：深夜 + 总工作时长 或 长时间无输入
TIRED_LATE_NIGHT_MINUTES = 60
TIRED_IDLE_MINUTES = 20       # 深夜长时间无输入

# 频繁切窗阈值：5 分钟内切换次数
WINDOW_SWITCH_THRESHOLD = 4


def classify_intent(
    period: str = "other",
    category: str = "other",
    activity: str = "idle",
    fg_duration_min: float = 0.0,
    conversation_idle_min: float = 0.0,
    window_switches_5min: int = 0,
    is_weekend: bool = False,
) -> dict:
    """核心分类函数（纯函数，便于测试）。

    Returns:
        {"intent": str, "scenario": str, "confidence": float, "reason": str}
    """
    late_night = period in LATE_NIGHT_PERIODS
    base_intent = CATEGORY_INTENT.get(category, "work")
    reason_parts = []

    # ── 1. 场景判定（最具体的优先）──

    # 深夜加班：深夜 + 工作类 + 持续 30 分钟以上
    if late_night and base_intent in ("work", "learn") and fg_duration_min >= LATE_NIGHT_WORK_MINUTES:
        return {
            "intent": "tense",
            "scenario": "late_night_work",
            "confidence": min(0.95, 0.7 + fg_duration_min / 200),
            "reason": f"深夜({period})仍在{category}持续{int(fg_duration_min)}分钟",
        }

    # 深夜无所事事：深夜 + 长时间无输入
    if late_night and activity == "idle" and conversation_idle_min >= TIRED_IDLE_MINUTES:
        return {
            "intent": "tired",
            "scenario": "late_night_all",
            "confidence": 0.8,
            "reason": f"深夜({period})长时间无输入({int(conversation_idle_min)}分钟)",
        }

    # 长时间工作该休息：工作类持续 90 分钟以上（非深夜）
    if base_intent == "work" and fg_duration_min >= LONG_WORK_MINUTES:
        return {
            "intent": "tired",
            "scenario": "long_work_break",
            "confidence": min(0.9, 0.65 + fg_duration_min / 300),
            "reason": f"{category}持续{int(fg_duration_min)}分钟，该休息了",
        }

    # 连续学习：browsing 持续 15 分钟以上
    if category == "browsing" and fg_duration_min >= LEARN_MINUTES:
        return {
            "intent": "learn",
            "scenario": "tutorial_follow",
            "confidence": min(0.85, 0.6 + fg_duration_min / 120),
            "reason": f"浏览持续{int(fg_duration_min)}分钟，可能在学东西",
        }

    # 频繁切窗口：5 分钟内切换 ≥ 4 次（可能卡住了/到处找东西）
    if window_switches_5min >= WINDOW_SWITCH_THRESHOLD:
        return {
            "intent": "tense",
            "scenario": "window_switch",
            "confidence": min(0.8, 0.5 + window_switches_5min / 10),
            "reason": f"5分钟切换{window_switches_5min}次窗口",
        }

    # 玩游戏
    if base_intent == "entertain" and category == "gaming":
        return {
            "intent": "entertain",
            "scenario": "gaming",
            "confidence": 0.9,
            "reason": f"在玩游戏({category})",
        }

    # 看视频/直播
    if base_intent == "entertain" and category == "entertainment":
        return {
            "intent": "entertain",
            "scenario": "video_watching",
            "confidence": 0.9,
            "reason": f"在看视频({category})",
        }

    # 长时间没说话（对话空闲 60 分钟 + 非娱乐）
    if conversation_idle_min >= 60 and base_intent != "entertain":
        return {
            "intent": "work",
            "scenario": "chat_idle",
            "confidence": 0.7,
            "reason": f"对话空闲{int(conversation_idle_min)}分钟",
        }

    # ── 2. 兜底：按基础意图 ──
    if is_weekend and base_intent == "entertain":
        return {
            "intent": "entertain",
            "scenario": "weekend_play",
            "confidence": 0.8,
            "reason": "周末娱乐",
        }

    # 清晨首次（6-9点 + 刚启动对话空闲小）
    if period == "morning" and conversation_idle_min < 10:
        return {
            "intent": "work",
            "scenario": "morning_first",
            "confidence": 0.7,
            "reason": "清晨刚开始",
        }

    return {
        "intent": base_intent,
        "scenario": "chat_idle",
        "confidence": 0.5,
        "reason": f"默认({category}/{period})",
    }
