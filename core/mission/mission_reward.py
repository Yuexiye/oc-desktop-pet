"""奖励结算器（03 §2.2.4 + §7.1）

把 MissionReward 落到 PetSave：
- credits   -> PetSave.money（通用货币，沿用现有经济，不另起 credits 字段）
- gacha_energy -> PetSave.gacha_energy（盲盒专用，封顶 gacha_energy_max）
- exp      -> PetSaveManager.add_exp（可能触发升级）
- gacha_tickets -> PetSave.gacha_tickets
- badge_id -> PetSave.badges（去重）
- items    -> 暂未接入物品系统，留 TODO

注意：调用方需保证线程安全（MissionManager 单锁串行）。
"""
from __future__ import annotations

import logging

from core.mission.mission import MissionReward

logger = logging.getLogger(__name__)


class MissionRewardGrantor:
    def __init__(self, save_mgr):
        self._save_mgr = save_mgr

    def grant(self, reward: MissionReward) -> None:
        if reward is None:
            return
        s = self._save_mgr.save

        if reward.credits:
            s.money = (s.money or 0.0) + reward.credits

        if reward.gacha_energy:
            s.gacha_energy = min(
                s.gacha_energy_max,
                (s.gacha_energy or 0.0) + reward.gacha_energy,
            )
            s.total_gacha_energy_earned = (
                s.total_gacha_energy_earned or 0.0
            ) + reward.gacha_energy

        if reward.gacha_tickets:
            s.gacha_tickets = (s.gacha_tickets or 0) + reward.gacha_tickets

        if reward.badge_id and reward.badge_id not in (s.badges or []):
            s.badges = list(s.badges or []) + [reward.badge_id]

        if reward.exp:
            try:
                self._save_mgr.add_exp(reward.exp)
            except Exception:
                logger.exception("add_exp failed during reward grant")

        if reward.items:
            # TODO: 接入 core.items 发放实物；当前仅记录日志
            logger.info("reward items (未发放，待接入物品系统): %s", reward.items)


__all__ = ["MissionRewardGrantor"]
