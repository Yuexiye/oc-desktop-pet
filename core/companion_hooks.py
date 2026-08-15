"""陪伴钩子 — 每日首启问候 + 关电脑离别语（P2 关系）

验收核心："连续用一周后，你关电脑时会觉得少了点什么"。

首启问候：跨天启动时，读昨日记忆 → 生成"接得上"的问候
    - 有昨日话题 → "你昨天说的「X」后来怎么样了？"
    - 无话题但有活动 → "昨天看你一直在忙 X，今天还要继续吗？"
    - 全新用户 → 基础问候
离别语：closeEvent / 关电脑时 → 温柔道别 + 存今日记忆（下次能接上）
    - "今天也辛苦了，明天见！"
    - 若陪伴 streak 较高 → "我们已经一起过了 X 天啦"

用法（由 pet.py 在启动/关闭时调用）:
    from core.companion_hooks import build_morning_greeting, build_farewell
    greet_text = build_morning_greeting(memory)          # 跨天首启
    farewell_text = build_farewell(memory)               # 关电脑
"""
from __future__ import annotations

import logging
import random
from datetime import datetime

logger = logging.getLogger(__name__)

# 前台分类 → 中文友好名（用于"昨天你在忙 X"）
CATEGORY_LABELS = {
    "writing": "写东西",
    "development": "写代码",
    "browsing": "浏览网页",
    "gaming": "玩游戏",
    "communication": "聊天",
    "entertainment": "看视频",
    "other": "处理事情",
}


def _cat_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, cat)


def build_morning_greeting(memory) -> str | None:
    """跨天首启问候。返回问候文本；非跨天/无记忆返回 None（不打扰）。

    memory: CompanionMemory 实例（应已 load()）
    """
    try:
        if not memory.is_new_day():
            return None
        yesterday = memory.yesterday_summary()
        last_topic = memory.last_topic
        hour = datetime.now().hour

        # 时段前缀
        if hour < 6:
            prefix = "这么早就醒啦？"
        elif hour < 12:
            prefix = "早安！"
        elif hour < 18:
            prefix = "午安～"
        else:
            prefix = "晚上好～"

        # 1. 有昨日话题 → 接话题（P2 验收核心："你昨天说的那个项目后来怎么样了？"）
        if last_topic:
            # 从话题里截一段（去掉过长的前缀）
            snippet = last_topic[:20]
            return random.choice([
                f"{prefix}你昨天说的「{snippet}」后来怎么样了？",
                f"{prefix}我还记得你昨天聊到「{snippet}」，有进展吗？",
            ])
        # 2. 无话题但有昨日活动 → 接活动
        if yesterday:
            return f"{prefix}昨天看你一直在忙，今天也要加油哦！"
        # 3. 全新用户 / 无昨日记录 → 基础问候
        return random.choice([
            f"{prefix}今天也要元气满满哦～",
            f"{prefix}我在这边等你呢。",
        ])
    except Exception as e:
        logger.debug("build_morning_greeting failed: %s", e)
        return None


def build_farewell(memory) -> str:
    """关电脑/退出时的离别语。同时会归档记忆（memory.close() 由调用方执行）。"""
    try:
        streak = memory.streak
        day_summary = memory.day_summary()
        # 有陪伴天数 → 强调陪伴关系（P2 验收：关电脑觉得少了点什么）
        if streak >= 3:
            return random.choice([
                f"今天也辛苦了！我们已经一起过了 {streak} 天啦，明天见！",
                f"晚安～记得明天还来找我玩哦（第 {streak} 天打卡）！",
            ])
        return random.choice([
            "今天也辛苦了，明天见！",
            "晚安，要好好休息呀～",
            "我在这边等你回来。",
        ])
    except Exception as e:
        logger.debug("build_farewell failed: %s", e)
        return "今天也辛苦了，明天见！"
