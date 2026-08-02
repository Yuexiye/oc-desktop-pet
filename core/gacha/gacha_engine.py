"""盲盒抽奖引擎（03 §3.4 + §5.1）

- 按权重随机抽取
- 各奖池独立保底：连续 guarantee_rare 抽未出 rare+ 则下一抽必出 rare+
- 直接修改传入 pool 的 pity_count（调用方负责持久化）
"""
from __future__ import annotations

import logging
import random

from core.gacha.gacha import GachaItem, GachaPool, GachaRarity

logger = logging.getLogger(__name__)

_RARE_AND_ABOVE = {
    GachaRarity.RARE,
    GachaRarity.EPIC,
    GachaRarity.LEGENDARY,
}


class GachaEngine:
    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()

    def draw(self, pool: GachaPool) -> GachaItem:
        items = pool.items
        if not items:
            raise ValueError("奖池为空，无法抽奖")

        force_rare = pool.pity_count >= pool.guarantee_rare

        if force_rare:
            candidates = [it for it in items if it.rarity in _RARE_AND_ABOVE]
            pool.pity_count = 0
        else:
            candidates = items

        weights = [max(it.weight, 0.0001) for it in candidates]
        chosen = self._rng.choices(candidates, weights=weights, k=1)[0]

        # 保底计数推进
        if chosen.rarity in _RARE_AND_ABOVE:
            pool.pity_count = 0
        else:
            pool.pity_count += 1

        return chosen


__all__ = ["GachaEngine"]
