"""盲盒系统（03 成长计划）

- gacha.py        数据模型：GachaRarity / GachaItem / GachaPool
- gacha_engine.py 抽奖引擎：按权重抽取 + 各池独立保底
- gacha_pools.py  奖池配置（标准盲盒）

能量消耗：单抽 cost_energy（默认 10）或 cost_tickets（默认 1 券）。
"""
from __future__ import annotations

from core.gacha.gacha import GachaItem, GachaPool, GachaRarity
from core.gacha.gacha_engine import GachaEngine
from core.gacha.gacha_pools import STANDARD_POOL

__all__ = [
    "GachaRarity",
    "GachaItem",
    "GachaPool",
    "GachaEngine",
    "STANDARD_POOL",
]
