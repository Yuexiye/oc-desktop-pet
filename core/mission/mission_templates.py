"""任务模板库（03 §4 每日 / 每周 / 成就）

模板即 Mission 定义（无运行时进度）。MissionGenerator 据此抽样实例化。

条件 type 取值来自 mission_tracker.EVENT_TO_CONDITION 的键；
只有事件总线里真正埋点的事件对应的任务才会推进，其余会停在 0（已在注释标注）。
"""
from __future__ import annotations

from core.mission.mission import (
    Mission,
    MissionCondition,
    MissionReward,
    MissionType,
)

# ------------------------------------------------------------------ 每日
DAILY_TEMPLATES: list[Mission] = [
    Mission(
        id="daily:chat", name="🗣️ 聊聊天", icon="🗣️",
        description="和桌宠对话 3 次", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="chat", count=3)],
        rewards=MissionReward(credits=30, gacha_energy=3),
        sort_order=10,
    ),
    Mission(
        id="daily:work", name="💼 工作一下", icon="💼",
        description="完成 1 次工作", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="work", count=1)],
        rewards=MissionReward(credits=50, gacha_energy=5),
        sort_order=20,
    ),
    Mission(
        id="daily:feed", name="🍎 喂食时间", icon="🍎",
        description="投喂桌宠 1 次", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="feed", count=1)],
        rewards=MissionReward(credits=20, gacha_energy=2),
        sort_order=30,
    ),
    Mission(
        id="daily:screen", name="👀 看看屏幕", icon="👀",
        description="触发 1 次屏幕感知", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="screen_capture", count=1)],
        rewards=MissionReward(credits=30, gacha_energy=3),
        sort_order=40,
    ),
    Mission(
        id="daily:phone", name="📱 手机互动", icon="📱",
        description="上报 1 次手机活动", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="phone_event", count=1)],
        rewards=MissionReward(credits=25, gacha_energy=3),
        sort_order=50,
    ),
    Mission(
        id="daily:window", name="🎪 窗口互动", icon="🎪",
        description="发生 1 次窗口互动", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="window_interact", count=1)],
        rewards=MissionReward(credits=25, gacha_energy=3),
        sort_order=60,
    ),
    Mission(
        id="daily:proactive", name="💡 主动搭话", icon="💡",
        description="触发 1 次主动对话", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="proactive", count=1)],
        rewards=MissionReward(credits=20, gacha_energy=2),
        sort_order=70,
    ),
    Mission(
        id="daily:multi", name="🤝 多宠互动", icon="🤝",
        description="发生 1 次多桌宠互动", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="multi_pet", count=1)],
        rewards=MissionReward(credits=25, gacha_energy=3),
        sort_order=80,
    ),
    Mission(
        id="daily:mood", name="😊 保持好心情", icon="😊",
        description="让桌宠心情达到 80", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="attribute", count=1, target="mood:80")],
        rewards=MissionReward(credits=20, gacha_energy=2),
        sort_order=90,
    ),
    Mission(
        id="daily:full", name="🍚 吃饱饱", icon="🍚",
        description="让桌宠饱食度达到 90", mission_type=MissionType.DAILY,
        conditions=[MissionCondition(type="attribute", count=1, target="hunger:90")],
        rewards=MissionReward(credits=20, gacha_energy=2),
        sort_order=100,
    ),
]

# ------------------------------------------------------------------ 每周
WEEKLY_TEMPLATES: list[Mission] = [
    Mission(
        id="weekly:work10", name="🏆 勤劳小能手", icon="🏆",
        description="累计完成 10 次工作", mission_type=MissionType.WEEKLY,
        conditions=[MissionCondition(type="work", count=10)],
        rewards=MissionReward(credits=200, gacha_energy=20, gacha_tickets=1),
        sort_order=10,
    ),
    Mission(
        id="weekly:multi3", name="🎪 多宠联动", icon="🎪",
        description="多桌宠互动 3 次", mission_type=MissionType.WEEKLY,
        conditions=[MissionCondition(type="multi_pet", count=3)],
        rewards=MissionReward(credits=200, gacha_energy=20),
        sort_order=20,
    ),
    Mission(
        id="weekly:item5", name="🌟 收集达人", icon="🌟",
        description="收集 5 种不同物品", mission_type=MissionType.WEEKLY,
        conditions=[MissionCondition(type="item_collect", count=5)],
        rewards=MissionReward(credits=150, gacha_energy=15),
        sort_order=30,
    ),
]

# ------------------------------------------------------------------ 成就（长期，不刷新）
ACHIEVEMENT_TEMPLATES: list[Mission] = [
    Mission(
        id="ach:firstchat", name="🥚 初来乍到", icon="🥚", hidden=True,
        description="完成首次对话", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="chat", count=1)],
        rewards=MissionReward(credits=100, badge_id="newbie"),
        sort_order=10,
    ),
    Mission(
        id="ach:work100", name="💪 勤劳致富", icon="💪", hidden=True,
        description="累计工作 100 次", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="work", count=100)],
        rewards=MissionReward(credits=500, badge_id="hardworker"),
        sort_order=20,
    ),
    Mission(
        id="ach:gacha100", name="🎰 盲盒达人", icon="🎰", hidden=True,
        description="开启盲盒 100 次", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="gacha_count", count=100)],
        rewards=MissionReward(credits=300, badge_id="gacha_master"),
        sort_order=30,
    ),
    Mission(
        id="ach:multi100", name="🤝 社交达人", icon="🤝", hidden=True,
        description="多桌宠互动 100 次", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="multi_pet", count=100)],
        rewards=MissionReward(credits=300, badge_id="social"),
        sort_order=40,
    ),
    Mission(
        id="ach:item50", name="🎁 收集大师", icon="🎁", hidden=True,
        description="收集 50 种物品", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="item_collect", count=50)],
        rewards=MissionReward(credits=400, badge_id="collector"),
        sort_order=50,
    ),
    Mission(
        id="ach:lv5", name="🌱 成长发芽", icon="🌱", hidden=True,
        description="桌宠升到 5 级", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="level_up", count=1, target="5")],
        # 注意：升级类奖励只给货币/能量/徽章，绝不给 exp，避免奖励结算重入 add_exp 递归
        rewards=MissionReward(credits=200, gacha_energy=10),
        sort_order=60,
    ),
    Mission(
        id="ach:lv10", name="🌳 茁壮成长", icon="🌳", hidden=True,
        description="桌宠升到 10 级", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="level_up", count=1, target="10")],
        rewards=MissionReward(credits=500, gacha_energy=20, badge_id="grown"),
        sort_order=70,
    ),
    Mission(
        id="ach:mood90", name="😄 开心果", icon="😄", hidden=True,
        description="让桌宠心情达到 90", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="attribute", count=1, target="mood:90")],
        rewards=MissionReward(credits=150, gacha_energy=10),
        sort_order=80,
    ),
    Mission(
        id="ach:energy80", name="🔋 元气满满", icon="🔋", hidden=True,
        description="让桌宠精力达到 80", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="attribute", count=1, target="energy:80")],
        rewards=MissionReward(credits=150, gacha_energy=10),
        sort_order=90,
    ),
    Mission(
        id="ach:health80", name="💖 健健康康", icon="💖", hidden=True,
        description="让桌宠健康达到 80", mission_type=MissionType.ACHIEVEMENT,
        conditions=[MissionCondition(type="attribute", count=1, target="health:80")],
        rewards=MissionReward(credits=150, gacha_energy=10),
        sort_order=100,
    ),
]


def all_templates() -> list[Mission]:
    return [*DAILY_TEMPLATES, *WEEKLY_TEMPLATES, *ACHIEVEMENT_TEMPLATES]


__all__ = [
    "DAILY_TEMPLATES",
    "WEEKLY_TEMPLATES",
    "ACHIEVEMENT_TEMPLATES",
    "all_templates",
]
