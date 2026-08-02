"""任务池（03 §2.2.3 + §3.2 刷新）

职责：
- 持有当前激活任务与其进度
- 每日 04:00 / 每周一 04:00 刷新每日 / 每周任务（成就任务常驻，不刷新）
- 进度持久化到 <agent_id>.missions.json（与存档同目录，避免污染主存档 schema）

线程安全：MissionManager 用单锁串行化 on_event / refresh / claim，这里不再单独加锁。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pydantic import BaseModel, Field

from core.mission.mission import Mission, MissionProgress
from core.mission.mission_generator import MissionGenerator

logger = logging.getLogger(__name__)

_REFRESH_HOUR = 4  # 每日/每周刷新时刻（与游戏行业惯例一致，见 03 §9 #4）


def _next_4am(now: float, weekly: bool = False) -> float:
    """计算下一个刷新时间戳（本地时区）。"""
    t = time.localtime(now)
    base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, _REFRESH_HOUR, 0, 0,
                        t.tm_wday, t.tm_yday, t.tm_isdst))
    if weekly:
        days_to_mon = (7 - t.tm_wday) % 7
        if days_to_mon == 0:
            days_to_mon = 7  # 今天已是周一 → 下周一
        return base + days_to_mon * 86400.0
    if now >= base:
        return base + 86400.0
    return base


class MissionPoolState(BaseModel):
    """可序列化的池状态。"""
    active: list[Mission] = Field(default_factory=list)
    progress: dict[str, MissionProgress] = Field(default_factory=dict)
    daily_refresh_at: float = 0.0
    weekly_refresh_at: float = 0.0


class MissionPool:
    def __init__(self, generator: MissionGenerator | None = None):
        self._generator = generator or MissionGenerator()
        self.active: dict[str, Mission] = {}
        self.progress: dict[str, MissionProgress] = {}
        self.daily_refresh_at: float = 0.0
        self.weekly_refresh_at: float = 0.0
        self.weekly_count: int = 3
        self.max_active: int = 20

    # -------------------------------------------------------------- 查询
    def active_missions(self) -> list[Mission]:
        return list(self.active.values())

    def get_mission(self, mid: str) -> Mission | None:
        return self.active.get(mid)

    def get_progress(self, mid: str) -> MissionProgress:
        prog = self.progress.get(mid)
        if prog is None:
            mission = self.active.get(mid)
            n = len(mission.conditions) if mission else 0
            prog = MissionProgress(mission_id=mid, condition_progress=[0] * n)
            if mission is not None:
                self.progress[mid] = prog
        return prog

    # -------------------------------------------------------------- 增删
    def add_mission(self, mission: Mission) -> None:
        self.active[mission.id] = mission
        if mission.id not in self.progress:
            self.progress[mission.id] = MissionProgress(
                mission_id=mission.id,
                condition_progress=[0] * len(mission.conditions),
            )

    def remove_mission(self, mid: str) -> None:
        self.active.pop(mid, None)
        self.progress.pop(mid, None)

    # -------------------------------------------------------------- 刷新
    def refresh_if_needed(self, now: float, level: int) -> None:
        if now >= self.daily_refresh_at:
            self._refresh_daily(now, level)
        if now >= self.weekly_refresh_at:
            self._refresh_weekly(now, level)

    def _refresh_prefix(self, prefix: str) -> None:
        for mid in [m for m in self.active if m.startswith(prefix)]:
            self.remove_mission(mid)

    def _refresh_daily(self, now: float, level: int) -> None:
        self._refresh_prefix("daily:")
        for m in self._generator.generate_daily(level):  # 全量，不随机截断
            self.add_mission(m)
        self.daily_refresh_at = _next_4am(now, weekly=False)
        logger.info("每日任务已刷新（level=%d）", level)

    def _refresh_weekly(self, now: float, level: int) -> None:
        self._refresh_prefix("weekly:")
        for m in self._generator.generate_weekly(level, self.weekly_count):
            self.add_mission(m)
        self.weekly_refresh_at = _next_4am(now, weekly=True)
        logger.info("每周任务已刷新（level=%d）", level)

    def ensure_achievements(self, achievements: list[Mission]) -> None:
        for m in achievements:
            if m.id not in self.active:
                self.add_mission(m)

    # -------------------------------------------------------------- 持久化
    def save_state(self, path: str | Path) -> None:
        state = MissionPoolState(
            active=list(self.active.values()),
            progress=self.progress,
            daily_refresh_at=self.daily_refresh_at,
            weekly_refresh_at=self.weekly_refresh_at,
        )
        tmp = Path(path).with_suffix(".missions.tmp.json")
        try:
            tmp.write_text(
                state.model_dump_json(indent=2), encoding="utf-8"
            )
            tmp.replace(Path(path))
        except Exception:
            logger.exception("mission state save failed: %s", path)

    def load_state(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            state = MissionPoolState(**data)
            self.active = {m.id: m for m in state.active}
            self.progress = dict(state.progress)
            self.daily_refresh_at = state.daily_refresh_at
            self.weekly_refresh_at = state.weekly_refresh_at
            return True
        except Exception:
            logger.exception("mission state load failed: %s", path)
            return False


__all__ = ["MissionPool", "MissionPoolState"]
