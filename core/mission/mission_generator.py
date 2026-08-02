"""任务生成器（03 §3 + Phase 1.8）

按等级过滤模板，随机抽 N 个实例化成 Mission（条件进度清零）。
"""
from __future__ import annotations

import copy
import logging
import random

from core.mission.mission import Mission, MissionType
from core.mission.mission_templates import (
    DAILY_TEMPLATES,
    WEEKLY_TEMPLATES,
)

logger = logging.getLogger(__name__)


class MissionGenerator:
    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()

    def _pick(
        self, templates: list[Mission], count: int, level: int
    ) -> list[Mission]:
        eligible = [t for t in templates if level >= t.level_limit]
        if not eligible:
            return []
        # 洗牌后取 count，等级高的模板优先（sort_order 小优先，作为稳定排序）
        pool = sorted(eligible, key=lambda m: m.sort_order)
        self._rng.shuffle(pool)
        chosen = pool[: max(0, min(count, len(pool)))]
        return [self._instantiate(m) for m in chosen]

    @staticmethod
    def _instantiate(tpl: Mission) -> Mission:
        """深拷贝模板并把条件进度清零，得到独立运行时实例。"""
        m = copy.deepcopy(tpl)
        for cond in m.conditions:
            cond.current = 0
        return m

    def generate_daily(self, level: int, count: int | None = None) -> list[Mission]:
        """生成每日任务。count 为 None 时返回全部符合条件模板（全量，
        确保窗口互动/主动对话等核心事件都有对应任务可推进）；
        传 count 则随机抽 N 个。"""
        n = count if count is not None else len(DAILY_TEMPLATES)
        return self._pick(DAILY_TEMPLATES, n, level)

    def generate_weekly(self, level: int, count: int = 3) -> list[Mission]:
        return self._pick(WEEKLY_TEMPLATES, count, level)


__all__ = ["MissionGenerator"]
