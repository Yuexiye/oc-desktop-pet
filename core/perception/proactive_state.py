# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""主动搭话节流状态 — 半衰期衰减 + 每日预算 + 文案去重。

`_half_life_for` / `_source_skip_probability` 直接搬自 N.E.K.O.
`main_logic/proactive_chat/state.py`（Apache 2.0），数值语义保持一致：
  - 硬跳过窗口（PROACTIVE_SOURCE_HARD_SKIP_SECONDS）内 skip_probability = 1.0
  - 窗口外按 kind 半衰期指数衰减：0.5 ** ((age - hard_skip) / half_life)

oc-pet 侧补充：
  - ProactiveThrottle 类把纯函数包装为可注入调度器的节流状态机
  - 每日预算（daily_limit）：跨天重置，达到即静默（tick 直接返回 None）
  - 近期文案去重（_is_similar_to_recent_proactive_chat）：同会话不重复触发

线程约束：本模块是纯 Python 状态，不含 Qt / asyncio / 网络 I/O；
由调用方（ProactiveScheduler，主线程）持有，后台线程不直接触碰。
"""
from __future__ import annotations

import difflib
import time
from collections import deque
from typing import Any

# ── 直接搬自 N.E.K.O. config/proactive_settings.py 的数值 ──
PROACTIVE_SOURCE_HARD_SKIP_SECONDS = 5 * 3600
"""主动搭话 source 衰减历史的硬窗口（p_skip=1.0）。5h 内同一候选必跳。"""

PROACTIVE_SOURCE_HALF_LIFE_BY_KIND: dict[str, float] = {
    "web": 3 * 86400.0,
    "image": 3 * 86400.0,
    "music": 1 * 86400.0,
}
"""硬窗口外按 kind 各自的 p_skip 半衰期（秒）。"""

PROACTIVE_SOURCE_HALF_LIFE_DEFAULT = 3 * 86400.0
"""未在 _BY_KIND 命中时的兜底半衰期。"""

PROACTIVE_SOURCE_FORGET_P = 0.05
"""p_skip 跌破此阈值即从衰减历史中遗忘（让文件体积自然有界）。"""

# oc-pet 默认每日主动搭话上限（与 proactive.py DAILY_LIMIT 对齐，可被 config 覆盖）
DEFAULT_DAILY_LIMIT = 20

# 近期文案去重窗口与相似度阈值（参考 N.E.K.O. state.py 数值）
RECENT_CHAT_MAX_AGE_SECONDS = 3600      # 1h 内的搭话记录
PROACTIVE_SIMILARITY_THRESHOLD = 0.90   # 90% 以上重复直接放弃本轮
RECENT_CHAT_HISTORY_MAX = 10


def _half_life_for(kind: str) -> float:
    """查 kind 对应的半衰期（秒）。未知 kind 用兜底值。"""
    return PROACTIVE_SOURCE_HALF_LIFE_BY_KIND.get(
        kind,
        PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    )


def _source_skip_probability(age: float, half_life: float) -> float:
    """硬跳过窗口内 1.0，窗口外按半衰期指数衰减。

    Args:
        age: 距上次使用经过的秒数（>=0）。
        half_life: 该 kind 的半衰期（秒）。

    Returns:
        0.0~1.0 的跳过概率。
    """
    if age < PROACTIVE_SOURCE_HARD_SKIP_SECONDS:
        return 1.0
    decay_age = age - PROACTIVE_SOURCE_HARD_SKIP_SECONDS
    return 0.5 ** (decay_age / half_life)


def _normalize_text_for_similarity(text: str) -> str:
    """文案相似度归一：小写 + 折叠连续空白。"""
    text = (text or "").strip().lower()
    return " ".join(text.split())


def _is_similar_to_recent_proactive_chat(
    history: "deque[tuple[float, str]] | None",
    message: str,
    *,
    now: float | None = None,
) -> tuple[bool, float]:
    """检查 message 是否与近期主动搭话高度相似（高阈值防误杀）。

    Args:
        history: deque([(ts, message), ...])，可为 None（无历史）。
        message: 待检查文案。
        now: 当前时间戳（测试可注入）。

    Returns:
        (is_duplicate, best_score)。
    """
    if not history or not (message or "").strip():
        return False, 0.0
    now = now if now is not None else time.time()
    current = _normalize_text_for_similarity(message)
    if not current:
        return False, 0.0
    best = 0.0
    for entry in history:
        try:
            ts, old_msg = entry[0], entry[1]
        except Exception:
            continue
        if now - float(ts) >= RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        old_norm = _normalize_text_for_similarity(old_msg)
        if not old_norm:
            continue
        score = difflib.SequenceMatcher(None, current, old_norm).ratio()
        if score > best:
            best = score
        if score >= PROACTIVE_SIMILARITY_THRESHOLD:
            return True, score
    return False, best


class ProactiveThrottle:
    """主动搭话节流状态机 — 半衰期 + 每日预算 + 近期去重。

    设计：
      - source 衰减历史：{source_key: {"ts": float, "kind": str}}，
        用 _source_skip_probability 判定是否应跳过该候选；
      - 每日预算：跨天重置；达到上限后 can_trigger_today() 返回 False；
      - 近期文案：deque(maxlen=RECENT_CHAT_HISTORY_MAX) 供同会话去重。

    用法（由 ProactiveScheduler 持有）：
        throttle = ProactiveThrottle(daily_limit=20)
        if not throttle.can_trigger_today():
            return None
        if throttle.should_skip("rule:写了这么久", kind="chat"):
            logger.info("throttled")
            return None
        throttle.record_used("rule:写了这么久", kind="chat")
        throttle.record_trigger()
    """

    def __init__(self, daily_limit: int = DEFAULT_DAILY_LIMIT):
        self._daily_limit = int(daily_limit) if daily_limit and daily_limit > 0 else DEFAULT_DAILY_LIMIT
        self._daily_count: int = 0
        self._daily_date: str = time.strftime("%Y-%m-%d")
        self._source_history: dict[str, dict[str, Any]] = {}
        self._recent_chats: "deque[tuple[float, str]]" = deque(maxlen=RECENT_CHAT_HISTORY_MAX)

    # ── 每日预算 ────────────────────────────────────────────

    def can_trigger_today(self, now: float | None = None) -> bool:
        """当天是否还可触发（跨天自动重置计数）。"""
        self._roll_date_if_needed(now)
        return self._daily_count < self._daily_limit

    def record_trigger(self, now: float | None = None) -> int:
        """记录一次成功触发，返回当天累计次数（>=daily_limit 后不再可触发）。"""
        self._roll_date_if_needed(now)
        self._daily_count += 1
        return self._daily_count

    def daily_count(self) -> int:
        return self._daily_count

    def daily_limit(self) -> int:
        return self._daily_limit

    def _roll_date_if_needed(self, now: float | None = None) -> None:
        today = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0

    # ── source 半衰期节流 ───────────────────────────────────

    def should_skip(self, source_key: str, kind: str = "chat", now: float | None = None) -> bool:
        """source_key 是否应被半衰期节流跳过。

        Args:
            source_key: 候选的唯一标识（如规则 prompt / 场景名）。
            kind: 半衰期分类（chat/web/image/music…，未知走默认半衰期）。
            now: 当前时间戳（测试可注入）。

        Returns:
            True=命中硬跳过窗口或按概率应跳过；False=可触发。
        """
        if not source_key:
            return False
        entry = self._source_history.get(source_key)
        if not entry:
            return False
        now = now if now is not None else time.time()
        try:
            age = now - float(entry.get("ts", 0.0) or 0.0)
            probability = _source_skip_probability(age, _half_life_for(kind))
        except (ValueError, TypeError, OverflowError):
            return False
        if probability >= 1.0:
            return True
        # 概率性跳过：随机 < probability 则跳过（半衰期衰减曲线）
        import random
        return random.random() < probability

    def record_used(self, source_key: str, kind: str = "chat", now: float | None = None) -> None:
        """记录某候选刚被使用（或刚被投递），驱动后续衰减。"""
        if not source_key:
            return
        now = now if now is not None else time.time()
        self._source_history[source_key] = {"ts": now, "kind": kind}
        # 遗忘：p_skip 跌破 FORGET_P 的旧条目直接清掉，防止无限增长
        forgotten = [
            key
            for key, entry in self._source_history.items()
            if _source_skip_probability(
                now - float(entry.get("ts", 0.0) or 0.0),
                _half_life_for(entry.get("kind", "chat")),
            ) < PROACTIVE_SOURCE_FORGET_P
        ]
        for key in forgotten:
            self._source_history.pop(key, None)

    # ── 近期文案去重（同会话不重复触发）────────────────────

    def record_chat(self, message: str, now: float | None = None) -> None:
        """记录一条已投递的主动搭话文案（供去重）。"""
        if not (message or "").strip():
            return
        self._recent_chats.append((now if now is not None else time.time(), message))

    def is_duplicate(self, message: str, now: float | None = None) -> bool:
        """message 是否与近期主动搭话文案高度重复。"""
        dup, _score = _is_similar_to_recent_proactive_chat(
            self._recent_chats,
            message,
            now=now,
        )
        return dup
