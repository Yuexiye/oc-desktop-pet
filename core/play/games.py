# -*- coding: utf-8 -*-
"""迷你小游戏纯逻辑 — 猜数字 / 石头剪刀布 / 快速反应（P2-3）

设计：
- 三个游戏都是**纯 Python 状态机**，无 Qt / 无 I/O / 无随机种子依赖之外的
  副作用，可直接 pytest 单测；UI 侧（``ui/mini_game_window.py``）只负责
  渲染与转发输入。
- 游戏结束统一返回 ``GameResult``（game/won/detail/meta），供 UI 弹结果、
  事件日志（复用 EventStream）与 EventBus 埋点。
- ``create_game(kind)`` / ``GAME_CATALOG`` 供邀请卡片与窗口工厂使用。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

# ── 游戏目录 ─────────────────────────────────────────────

GAME_CATALOG: dict[str, dict] = {
    "guess_number": {
        "id": "guess_number",
        "name": "猜数字",
        "icon": "🔢",
        "desc": "1~100 之间猜一个数，10 次机会，看谁先猜中～",
        "card_title": "🔢 猜数字",
        "card_body": "我在 1~100 之间想了一个数，10 次机会猜中它！",
    },
    "rps": {
        "id": "rps",
        "name": "石头剪刀布",
        "icon": "✊✋✌️",
        "desc": "石头剪刀布，三局两胜，输了要请我喝奶茶哦～",
        "card_title": "✊✋✌️ 石头剪刀布",
        "card_body": "来猜拳！石头剪刀布，看谁赢得多～",
    },
    "reaction": {
        "id": "reaction",
        "name": "快速反应",
        "icon": "⚡",
        "desc": "屏幕变绿瞬间点击，测测你的反应速度！",
        "card_title": "⚡ 快速反应",
        "card_body": "屏幕变绿的一瞬间点下去，测测你的反应有多快！",
    },
}


@dataclass
class GameResult:
    """一次对局结果的统一载体。

    Attributes:
        game: 游戏 id（guess_number / rps / reaction）
        won: 是否算"赢"（反应游戏：有效完成即 True）
        detail: 结果描述（气泡/结果标签用，中文）
        meta: 附加字段（尝试次数/比分/反应毫秒等）
    """

    game: str
    won: bool
    detail: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转 dict，供事件日志 / EventBus 透传。"""
        return {
            "game": self.game,
            "won": self.won,
            "detail": self.detail,
            "meta": dict(self.meta),
        }


# ── 猜数字 ───────────────────────────────────────────────


class GuessNumberGame:
    """猜数字：1~100 随机目标，最多 max_attempts 次。

    状态机：
      - ``guess(n)``：返回 ``{"status": "win"|"continue"|"invalid",
        "hint": "higher"|"lower"|"correct"|"", "attempts": int,
        "target": int(仅 win 时)}``
      - 猜中或次数耗尽后 ``finished`` 为 True，再次 guess 返回 invalid。
    """

    LOW = 1
    HIGH = 100

    def __init__(self, low: int = 1, high: int = 100, max_attempts: int = 10,
                 seed: Optional[int] = None) -> None:
        self._low = int(low)
        self._high = int(high)
        self._max_attempts = max(1, int(max_attempts))
        self._rng = random.Random(seed)
        self._target: int = 0
        self._attempts: int = 0
        self._finished: bool = False
        self._last_guess: Optional[int] = None
        self._reset_target()

    def _reset_target(self) -> None:
        self._target = self._rng.randint(self._low, self._high)
        self._attempts = 0
        self._finished = False
        self._last_guess = None

    @property
    def target(self) -> int:
        return self._target

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def attempts_left(self) -> int:
        return max(0, self._max_attempts - self._attempts)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def low(self) -> int:
        return self._low

    @property
    def high(self) -> int:
        return self._high

    def guess(self, number: int) -> dict:
        """提交一次猜测。

        Returns:
            dict: status（win/continue/invalid）、hint、attempts、target。
            游戏结束后再猜返回 invalid。
        """
        if self._finished:
            return {"status": "invalid", "hint": "", "attempts": self._attempts,
                    "target": self._target}
        try:
            n = int(number)
        except (TypeError, ValueError):
            return {"status": "invalid", "hint": "", "attempts": self._attempts,
                    "target": self._target}
        if n < self._low or n > self._high:
            return {"status": "invalid", "hint": "", "attempts": self._attempts,
                    "target": self._target}
        self._attempts += 1
        self._last_guess = n
        if n == self._target:
            self._finished = True
            return {"status": "win", "hint": "correct", "attempts": self._attempts,
                    "target": self._target}
        hint = "higher" if n < self._target else "lower"
        if self._attempts >= self._max_attempts:
            self._finished = True
            return {"status": "lose", "hint": hint, "attempts": self._attempts,
                    "target": self._target}
        return {"status": "continue", "hint": hint, "attempts": self._attempts,
                "target": self._target}

    def result(self) -> GameResult:
        """按当前状态生成 GameResult（win/lose/未结束）。"""
        won = self._finished and self._last_guess == self._target
        if self._finished:
            if self._attempts <= 0:
                detail = "还没开始就结束啦？"
            elif won:
                detail = f"猜中啦！用了 {self._attempts} 次～"
            else:
                detail = f"没猜中，答案是 {self._target}。再来一局？"
        else:
            detail = f"还没猜中哦（已猜 {self._attempts} 次）"
        return GameResult(game="guess_number", won=won, detail=detail,
                          meta={"attempts": self._attempts, "target": self._target})

    def reset(self) -> None:
        """重置新目标。"""
        self._reset_target()


# ── 石头剪刀布 ───────────────────────────────────────────

RPS_CHOICES: tuple[str, ...] = ("rock", "paper", "scissors")
RPS_EMOJI: dict[str, str] = {"rock": "✊", "paper": "✋", "scissors": "✌️"}
RPS_NAMES: dict[str, str] = {"rock": "石头", "paper": "布", "scissors": "剪刀"}

# 玩家出招 → 胜负（True=玩家赢）
_RPS_BEATS: dict[str, str] = {"rock": "scissors", "scissors": "paper", "paper": "rock"}


class RockPaperScissorsGame:
    """石头剪刀布：连续对局计分，三局两胜（best_of=3 时先赢 2 局获胜）。"""

    def __init__(self, best_of: int = 3, seed: Optional[int] = None) -> None:
        self._best_of = max(1, int(best_of))
        self._wins_needed = (self._best_of // 2) + 1
        self._rng = random.Random(seed)
        self._player_score = 0
        self._pet_score = 0
        self._draw_score = 0
        self._rounds = 0
        self._finished = False
        self._last_result: Optional[dict] = None

    @property
    def best_of(self) -> int:
        return self._best_of

    @property
    def wins_needed(self) -> int:
        return self._wins_needed

    @property
    def player_score(self) -> int:
        return self._player_score

    @property
    def pet_score(self) -> int:
        return self._pet_score

    @property
    def draw_score(self) -> int:
        return self._draw_score

    @property
    def rounds(self) -> int:
        return self._rounds

    @property
    def finished(self) -> bool:
        return self._finished

    def play(self, player_choice: str) -> dict:
        """玩家出招 → 桌宠随机出招 → 判定胜负。

        Returns:
            dict: {"player", "pet", "pet_emoji", "result": "win"|"lose"|"draw",
                   "player_score", "pet_score", "draw_score", "finished",
                   "winner": "player"|"pet"|""}
        """
        if self._finished:
            return self._last_result or {}
        choice = str(player_choice or "").strip().lower()
        if choice not in RPS_CHOICES:
            return {"player": choice, "pet": "", "pet_emoji": "", "result": "invalid",
                    "player_score": self._player_score, "pet_score": self._pet_score,
                    "draw_score": self._draw_score, "finished": self._finished,
                    "winner": ""}
        pet = self._rng.choice(RPS_CHOICES)
        self._rounds += 1
        if choice == pet:
            result = "draw"
            self._draw_score += 1
        elif _RPS_BEATS[choice] == pet:
            result = "win"
            self._player_score += 1
        else:
            result = "lose"
            self._pet_score += 1
        winner = ""
        if self._player_score >= self._wins_needed:
            self._finished = True
            winner = "player"
        elif self._pet_score >= self._wins_needed:
            self._finished = True
            winner = "pet"
        self._last_result = {
            "player": choice, "pet": pet, "pet_emoji": RPS_EMOJI.get(pet, ""),
            "result": result, "player_score": self._player_score,
            "pet_score": self._pet_score, "draw_score": self._draw_score,
            "finished": self._finished, "winner": winner,
        }
        return dict(self._last_result)

    def result(self) -> GameResult:
        won = self._finished and self._player_score >= self._wins_needed
        detail = (
            f"你 {self._player_score} : {self._pet_score} 我"
            + ("，你赢啦！🎉" if won else "，我赢啦～再来一局？")
            if self._finished
            else f"当前你 {self._player_score} : {self._pet_score} 我"
        )
        return GameResult(
            game="rps", won=won, detail=detail,
            meta={"player_score": self._player_score, "pet_score": self._pet_score,
                  "draw_score": self._draw_score, "rounds": self._rounds},
        )

    def reset(self) -> None:
        self._player_score = 0
        self._pet_score = 0
        self._draw_score = 0
        self._rounds = 0
        self._finished = False
        self._last_result = None


# ── 快速反应 ─────────────────────────────────────────────


class ReactionGame:
    """快速反应：arm() 后进入"等待信号"状态，record() 测反应毫秒。

    时序（由 UI 用 QTimer 实现视觉延迟，逻辑本身权威）：
      - ``arm(now, delay_sec)``：记录信号出现时刻 ``_go_at = now + delay_sec``
      - ``record(now)``：
          now < _go_at      → too_soon（抢跑）
          now >= _go_at     → ok（返回毫秒，round((now-_go_at)*1000)）
      - ``finished``：完成一次有效测量或抢跑后为 True；reset() 重新开始。
    """

    def __init__(self) -> None:
        self._armed = False
        self._go_at: float = 0.0
        self._finished = False
        self._last_ms: int = 0
        self._too_soon = False

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def last_ms(self) -> int:
        return self._last_ms

    @property
    def too_soon(self) -> bool:
        return self._too_soon

    def arm(self, now: float, delay_sec: float = 0.0) -> None:
        """进入待测状态。``delay_sec`` 为信号出现前的等待秒数。"""
        self._armed = True
        self._go_at = float(now) + max(0.0, float(delay_sec))
        self._finished = False
        self._too_soon = False
        self._last_ms = 0

    def record(self, now: float) -> dict:
        """记录一次点击。

        Returns:
            dict: {"status": "ok"|"too_soon"|"invalid", "ms": int,
                   "finished": bool}
        """
        if not self._armed:
            return {"status": "invalid", "ms": 0, "finished": self._finished}
        now = float(now)
        if now < self._go_at:
            self._armed = False
            self._finished = True
            self._too_soon = True
            self._last_ms = 0
            return {"status": "too_soon", "ms": 0, "finished": True}
        ms = int(round((now - self._go_at) * 1000.0))
        self._armed = False
        self._finished = True
        self._last_ms = ms
        return {"status": "ok", "ms": ms, "finished": True}

    def result(self) -> GameResult:
        if self._too_soon:
            won = False
            detail = "抢跑啦！等信号变绿再点～"
        elif self._finished and self._last_ms > 0:
            won = True
            detail = f"反应速度 {self._last_ms} ms！"
            if self._last_ms <= 250:
                detail += " 超快！⚡"
            elif self._last_ms <= 400:
                detail += " 不错哦～"
            else:
                detail += " 再练练？"
        else:
            won = False
            detail = "还没开始呢"
        return GameResult(game="reaction", won=won, detail=detail,
                          meta={"ms": self._last_ms, "too_soon": self._too_soon})

    def reset(self) -> None:
        self._armed = False
        self._go_at = 0.0
        self._finished = False
        self._last_ms = 0
        self._too_soon = False


# ── 工厂 ─────────────────────────────────────────────────

def create_game(kind: str, **kwargs):
    """按游戏 id 创建游戏实例；未知 id 返回 None（调用方防御处理）。"""
    kind = str(kind or "").strip().lower()
    if kind == "guess_number":
        return GuessNumberGame(**kwargs)
    if kind == "rps":
        return RockPaperScissorsGame(**kwargs)
    if kind == "reaction":
        return ReactionGame(**kwargs)
    return None


__all__ = [
    "GAME_CATALOG",
    "GameResult",
    "GuessNumberGame",
    "RPS_CHOICES",
    "RPS_EMOJI",
    "RPS_NAMES",
    "ReactionGame",
    "RockPaperScissorsGame",
    "create_game",
]
