"""任务系统编排器（MissionManager）

把 pool / tracker / grantor / generator 串起来：
- start()：加载持久化进度 -> 确保成就任务在池 -> 刷新每日/每周 -> 订阅事件总线
- 暴露 refresh() / get_active() / open_gacha() 供 pet.py 与 UI 调用
- 把盲盒抽奖作为 gacha_energy 的消费出口（双货币闭环）

所有对池的修改都走 self._lock（RLock），与 tracker 的锁独立但都保护共享状态。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from core.event_bus import EventBus
from core.gacha.gacha import GachaItem, GachaPool
from core.gacha.gacha_engine import GachaEngine
from core.gacha.gacha_pools import STANDARD_POOL
from core.mission.mission import Mission, MissionProgress
from core.mission.mission_generator import MissionGenerator
from core.mission.mission_pool import MissionPool
from core.mission.mission_reward import MissionRewardGrantor
from core.mission.mission_templates import ACHIEVEMENT_TEMPLATES
from core.mission.mission_tracker import MissionTracker

logger = logging.getLogger(__name__)


class MissionManager:
    def __init__(self, save_mgr, state_mgr=None):
        self._save_mgr = save_mgr
        self._state_mgr = state_mgr
        self._generator = MissionGenerator()
        self._pool = MissionPool(self._generator)
        self._grantor = MissionRewardGrantor(save_mgr)
        self._tracker = MissionTracker(self._pool, self._grantor)
        self._gacha_engine = GachaEngine()
        self._lock = threading.RLock()
        self._started = False

        save_path = getattr(save_mgr, "save_path", None)
        self._state_path = (
            str(Path(save_path).with_suffix(".missions.json")) if save_path else None
        )

    # -------------------------------------------------------------- 生命周期
    def start(self) -> None:
        if self._started:
            return
        if self._state_path:
            self._pool.load_state(self._state_path)

        # 保底计数持久化：把存档里的 gacha_pity 载入模块级单例池
        STANDARD_POOL.pity_count = int(getattr(self._save_mgr.save, "gacha_pity", 0) or 0)

        self._pool.ensure_achievements(ACHIEVEMENT_TEMPLATES)
        self._pool.refresh_if_needed(time.time(), int(self._save_mgr.save.level))
        self._tracker.subscribe()
        self._started = True
        self._save_state()
        logger.info("MissionManager started: %d 个激活任务",
                    len(self._pool.active))

    def stop(self) -> None:
        self._save_state()

    # -------------------------------------------------------------- 查询/刷新
    def refresh(self) -> None:
        with self._lock:
            self._pool.refresh_if_needed(time.time(), int(self._save_mgr.save.level))
            self._save_state()

    def get_active(self) -> list[tuple[Mission, MissionProgress]]:
        with self._lock:
            return [
                (m, self._pool.get_progress(m.id))
                for m in self._pool.active_missions()
            ]

    # -------------------------------------------------------------- 盲盒
    def open_gacha(self, pool: Optional[GachaPool] = None,
                   count: int = 1) -> Optional[list[GachaItem]]:
        """开启盲盒。count>1 即十连（一次扣足资源，循环抽取）。

        返回抽中的 GachaItem 列表；资源不足返回 None。
        """
        pool = pool or STANDARD_POOL
        count = max(1, int(count))
        s = self._save_mgr.save
        with self._lock:
            need_tickets = pool.cost_tickets * count
            need_energy = pool.cost_energy * count
            used_ticket = False
            if (s.gacha_tickets or 0) >= need_tickets:
                s.gacha_tickets -= need_tickets
                used_ticket = True
            elif (s.gacha_energy or 0.0) >= need_energy:
                s.gacha_energy -= need_energy
            else:
                logger.info("盲盒资源不足（需 %g×%d 能量或 %d×%d 券）",
                            pool.cost_energy, count, pool.cost_tickets, count)
                return None

            items: list[GachaItem] = []
            for _ in range(count):
                item = self._gacha_engine.draw(pool)
                self._apply_gacha_item(item)
                items.append(item)
                EventBus.emit("gacha_opened", pool_id=pool.id,
                              rarity=item.rarity.value, count=count)

            # 保底计数持久化
            s.gacha_pity = STANDARD_POOL.pity_count
            self._save_state()
            logger.info("盲盒开启×%d: 最高稀有度=%s (用券=%s)",
                        count, max((it.rarity.value for it in items), default="?"),
                        used_ticket)
            return items

    def get_gacha_status(self) -> dict:
        """返回盲盒资源与保底进度（供 UI 展示）"""
        s = self._save_mgr.save
        pity_left = max(0, STANDARD_POOL.guarantee_rare - STANDARD_POOL.pity_count)
        return {
            "energy": float(getattr(s, "gacha_energy", 0) or 0),
            "tickets": int(getattr(s, "gacha_tickets", 0) or 0),
            "pity_left": pity_left,
            "pity_total": STANDARD_POOL.guarantee_rare,
            "cost_energy": STANDARD_POOL.cost_energy,
            "cost_tickets": STANDARD_POOL.cost_tickets,
        }

    def get_collection(self) -> dict:
        """返回收集册数据：全部可收集物 + 已收集集合"""
        s = self._save_mgr.save
        all_items = [it for it in STANDARD_POOL.items
                     if it.item_type in ("item", "costume", "character")]
        collected = set(getattr(s, "collected_items", []) or [])
        return {"all": all_items, "collected": collected}

    def _apply_gacha_item(self, item: GachaItem) -> None:
        s = self._save_mgr.save
        if item.item_type == "energy":
            s.gacha_energy = min(
                s.gacha_energy_max, (s.gacha_energy or 0.0) + item.amount
            )
            s.total_gacha_energy_earned = (
                s.total_gacha_energy_earned or 0.0
            ) + item.amount
        elif item.item_type == "badge":
            if item.item_id and item.item_id not in (s.badges or []):
                s.badges = list(s.badges or []) + [item.item_id]
        elif item.item_type in ("item", "costume", "character"):
            # 虚拟收集物：抽中即入册（item_collected 事件供任务系统去重统计）
            cid = item.item_id
            if cid:
                if cid not in (s.collected_items or []):
                    s.collected_items = list(s.collected_items or []) + [cid]
                # P3 换装：costume 抽中即自动装备（equipped_costumes 持久化）
                if item.item_type == "costume":
                    equip = dict(getattr(s, "equipped_costumes", {}) or {})
                    if cid not in equip:
                        equip[cid] = time.time()
                        s.equipped_costumes = equip
                if item.item_type == "item":
                    EventBus.emit(
                        "item_collected", target=item.item_id, name=item.name
                    )

    # -------------------------------------------------------------- 持久化
    def _save_state(self) -> None:
        if self._state_path:
            self._pool.save_state(self._state_path)


__all__ = ["MissionManager"]
