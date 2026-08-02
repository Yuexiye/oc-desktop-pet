"""任务系统（03 成长计划）

包结构：
- mission.py          数据模型：Mission / MissionCondition / MissionReward / MissionProgress
- mission_templates.py 任务模板库（每日 / 每周 / 成就）
- mission_pool.py     任务池：激活、刷新、进度持久化
- mission_tracker.py  进度追踪：订阅事件总线、推进条件、判定完成
- mission_generator.py 任务生成：按等级从模板抽样
- mission_reward.py   奖励结算：发放双货币 / 经验 / 徽章
- mission_manager.py   编排：把上面串起来，订阅事件总线，暴露刷新/领取

设计原则（见 03 §2.3）：
- 任务系统只「监听」现有系统的事件，不修改感知/工作/对话的数据流（零侵入）。
- 双货币：通用货币复用 PetSave.money（03 的 credits），盲盒能量为新增 gacha_energy。
"""
from __future__ import annotations

from core.mission.mission import (
    Mission,
    MissionCondition,
    MissionProgress,
    MissionReward,
    MissionType,
)
from core.mission.mission_manager import MissionManager
from core.mission.mission_pool import MissionPool
from core.mission.mission_tracker import MissionTracker

__all__ = [
    "Mission",
    "MissionCondition",
    "MissionReward",
    "MissionProgress",
    "MissionType",
    "MissionPool",
    "MissionTracker",
    "MissionManager",
]
