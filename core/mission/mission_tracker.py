"""进度追踪器（03 §3.3 + §7.2）

订阅事件总线的现有事件，把事件映射到任务条件并推进进度：
- 匹配 active 中未完成的任务条件
- 满足条件计数 +1
- 全部条件满足 -> 判定完成 -> 结算奖励 -> 发 mission_completed

线程安全：on_event 用可重入锁（奖励结算可能间接触发其他事件，避免自死锁）。
"""
from __future__ import annotations

import logging
import threading
import time

from core.event_bus import EventBus
from core.mission.mission import Mission
from core.mission.mission_reward import MissionRewardGrantor

logger = logging.getLogger(__name__)


class MissionTracker:
    # 事件名 -> 任务条件 type（键即埋点事件名）
    EVENT_TO_CONDITION: dict[str, str] = {
        "chat_completed": "chat",
        "work_completed": "work",
        "item_used": "feed",
        "screen_analyzed": "screen_capture",
        "proactive_triggered": "proactive",
        "window_interacted": "window_interact",
        "phone_event": "phone_event",
        "level_up": "level_up",
        "attribute_changed": "attribute",
        "gacha_opened": "gacha_count",
        "multi_pet_event": "multi_pet",
        "item_collected": "item_collect",
        # 以下为连续量事件（需对应模块埋点后才推进）
        "idle_minutes": "idle_time",
        "work_minutes": "work_time",
        "emoji_react": "emoji_react",
    }

    def __init__(self, pool, grantor: MissionRewardGrantor):
        self._pool = pool
        self._grantor = grantor
        self._lock = threading.RLock()
        self._subscribers: dict[str, callable] = {}  # evt -> handler（供 unsubscribe）

    # -------------------------------------------------------------- 订阅
    def subscribe(self) -> None:
        if self._subscribers:
            return  # 幂等：已订阅不重复注册（防多开累积）
        for evt in self.EVENT_TO_CONDITION:
            handler = self._handler_for(evt)
            self._subscribers[evt] = handler
            EventBus.on(evt, handler)

    def unsubscribe(self) -> None:
        """取消全部订阅（关闭/stop 时调用，防止全局注册表累积 + 回调已销毁实例）。"""
        for evt, handler in list(self._subscribers.items()):
            try:
                EventBus.off(evt, handler)
            except Exception:
                logger.debug("EventBus.off 失败: %s", evt, exc_info=True)
        self._subscribers.clear()

    def _handler_for(self, evt: str):
        def _h(**data):
            self.on_event(evt, **data)
        return _h

    # -------------------------------------------------------------- 核心
    def on_event(self, event_name: str, **data) -> None:
        cond_type = self.EVENT_TO_CONDITION.get(event_name)
        if not cond_type:
            return
        target = data.get("target") or ""
        # 升级/属性类事件携带"当前等级"，用于 target 一次性达成判定
        evt_level = data.get("level") or 0

        with self._lock:
            for mission in self._pool.active_missions():
                if not mission.id:
                    continue
                prog = self._pool.get_progress(mission.id)
                if prog.completed:
                    continue
                advanced = False
                for i, cond in enumerate(mission.conditions):
                    if cond.type != cond_type:
                        continue
                    if cond.target and target and cond.target != target:
                        continue
                    # 升级类：带 target（达到某等级），等级达标即一次性达成，
                    # 不再按次数累加（避免每升一级都 +1 导致条件提前满足）。
                    if cond_type == "level_up" and cond.target:
                        try:
                            reached = evt_level >= int(cond.target)
                        except (TypeError, ValueError):
                            reached = False
                        if reached and prog.condition_progress[i] < 1:
                            prog.condition_progress[i] = 1
                            advanced = True
                        continue
                    # 属性类：target 格式 "<attr>:<threshold>"，例 "mood:80"。
                    # 事件 attribute_changed 携带 attribute=<属性名>, value=<当前值>，
                    # 当属性名匹配且值 >= 阈值即一次性达成。
                    if cond_type == "attribute" and cond.target:
                        try:
                            _an, _thr = cond.target.split(":", 1)
                            _thr = float(_thr)
                        except (ValueError, AttributeError):
                            continue
                        if data.get("attribute") != _an:
                            continue
                        try:
                            _val = float(data.get("value", 0) or 0)
                        except (TypeError, ValueError):
                            _val = 0.0
                        if _val >= _thr and prog.condition_progress[i] < 1:
                            prog.condition_progress[i] = 1
                            advanced = True
                        continue
                    # 收集类：按不同 target 去重（如"收集 5 种不同物品"），
                    # 同一物品重复获得不重复计数。
                    if cond_type == "item_collect":
                        seen = set(prog.distinct_targets or [])
                        if target and target not in seen:
                            seen.add(target)
                            prog.distinct_targets = list(seen)
                            if prog.condition_progress[i] < cond.count:
                                prog.condition_progress[i] += 1
                                advanced = True
                        continue
                    if prog.condition_progress[i] < cond.count:
                        prog.condition_progress[i] += 1
                        advanced = True
                if advanced and self._is_done(mission, prog):
                    prog.completed = True
                    prog.completed_at = time.time()
                    self._complete(mission, prog)

    @staticmethod
    def _is_done(mission: Mission, prog) -> bool:
        return all(
            prog.condition_progress[i] >= c.count
            for i, c in enumerate(mission.conditions)
        )

    def _complete(self, mission: Mission, prog) -> None:
        try:
            self._grantor.grant(mission.rewards)
            prog.claimed = True
            prog.claimed_at = time.time()
        except Exception:
            logger.exception("reward grant failed: %s", mission.id)
        try:
            EventBus.emit(
                "mission_completed",
                mission_id=mission.id,
                name=mission.name,
                rewards=mission.rewards.model_dump(),
            )
        except Exception:
            logger.exception("emit mission_completed failed")
        logger.info("任务完成: %s (%s)", mission.name, mission.id)


__all__ = ["MissionTracker"]
