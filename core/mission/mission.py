"""任务系统数据模型

字段命名约定（避开 PetSave 已有字段的坑，见 oc-pet-矛盾对照.md）：
- 奖励里的 gacha_energy = 盲盒能量，对应 PetSave.gacha_energy（不要叫 energy，会覆盖精力属性）
- 奖励里的 credits = 通用货币，结算时计入 PetSave.money（不新增 credits 字段）
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MissionType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    ACHIEVEMENT = "achievement"
    CHAIN = "chain"
    EVENT = "event"


class MissionCondition(BaseModel):
    """任务完成条件

    条件类型（type）映射到现有系统事件，见 mission_tracker.EVENT_TO_CONDITION：
        chat / work / feed / screen_capture / proactive / window_interact
        phone_event / level_up / attribute / gacha_count / multi_pet / item_collect
        idle_time / work_time / emoji_react
    target 为可选过滤（如指定 work_id / item_id），为空表示不限定。
    """
    type: str
    target: str = ""
    count: int = 1
    current: int = 0


class MissionReward(BaseModel):
    """任务奖励

    credits   -> 计入 PetSave.money（通用货币）
    gacha_energy -> 计入 PetSave.gacha_energy（盲盒专用）
    """
    credits: float = 0.0
    gacha_energy: float = 0.0
    exp: float = 0.0
    items: list[str] = Field(default_factory=list)
    gacha_tickets: int = 0
    badge_id: str = ""


class Mission(BaseModel):
    """任务定义（模板实例化后的运行时态）"""
    id: str
    name: str
    description: str = ""
    mission_type: MissionType
    conditions: list[MissionCondition] = Field(default_factory=list)
    rewards: MissionReward = Field(default_factory=MissionReward)
    prerequisites: list[str] = Field(default_factory=list)
    level_limit: int = 0
    expire_at: float = 0.0
    icon: str = "📋"
    hidden: bool = False
    repeatable: bool = False
    sort_order: int = 0


class MissionProgress(BaseModel):
    """任务进度追踪（与 Mission 一一对应，独立持久化）"""
    mission_id: str
    condition_progress: list[int] = Field(default_factory=list)
    completed: bool = False
    completed_at: float = 0.0
    claimed: bool = False
    claimed_at: float = 0.0
    # 已收集到的不同 target 集合（如 item_collect 的不同物品 id），用于"不同物品"去重统计
    distinct_targets: list[str] = Field(default_factory=list)


__all__ = [
    "MissionType",
    "MissionCondition",
    "MissionReward",
    "Mission",
    "MissionProgress",
]
