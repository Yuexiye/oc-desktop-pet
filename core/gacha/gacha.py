"""盲盒数据模型（03 §2.2.5）

盲盒消耗能量（gacha_energy）或盲盒券（gacha_tickets）。
奖池按权重抽取，稀有度以上走保底计数。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class GachaRarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"


class GachaItem(BaseModel):
    """盲盒奖品定义"""
    id: str
    name: str
    rarity: GachaRarity
    item_type: str = "item"   # item / costume / badge / character / energy
    item_id: str = ""         # 关联物品/角色 ID（item_type=energy 时为空）
    description: str = ""
    icon: str = "🎁"
    weight: float = 1.0
    amount: float = 10.0       # item_type=energy 时的能量数量


class GachaPool(BaseModel):
    """盲盒奖池"""
    id: str
    name: str = "标准盲盒"
    cost_energy: float = 10.0
    cost_tickets: int = 1
    items: list[GachaItem] = Field(default_factory=list)
    guarantee_rare: int = 10      # 每 N 抽必出 rare+
    pity_count: int = 0           # 当前保底计数
    daily_free: int = 1
    daily_free_used: int = 0


__all__ = ["GachaRarity", "GachaItem", "GachaPool"]
