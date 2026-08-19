# -*- coding: utf-8 -*-
"""交互意图检测 + 卡片构建 + 事件记录（P2-3/P2-4/P2-5 共用的纯逻辑）

职责：
- ``InteractionIntentDetector.detect(text)``：从用户聊天文本识别交互意图
  （game / music / rest），命中返回 ``InteractionIntent``，未命中返回 None。
- ``build_game_invite_card()`` / ``build_music_card(track)`` /
  ``build_rest_card(suggestion)``：把推荐结果构造成统一卡片 dict
  （kind/title/body/actions），供 UI（InteractionCard）直接消费。
- ``record_interaction_event(...)``：把交互行为写入 EventBus + 事件流
  （经注入的 record_event 回调，复用 EventStream）。

全部纯 Python，无 Qt 依赖。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.event_bus import EventBus
from core.play.break_reminder import BreakSuggestion
from core.play.games import GAME_CATALOG
from core.play.music_recommender import MusicTrack

logger = logging.getLogger(__name__)

# ── 意图类型 ─────────────────────────────────────────────

INTENT_GAME = "game"
INTENT_MUSIC = "music"
INTENT_REST = "rest"
VALID_INTENTS = (INTENT_GAME, INTENT_MUSIC, INTENT_REST)


@dataclass
class InteractionIntent:
    """一次识别的交互意图。"""

    kind: str                    # game / music / rest
    confidence: float = 1.0
    matched: str = ""            # 命中的关键词
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "confidence": self.confidence,
                "matched": self.matched, "payload": dict(self.payload)}


# 游戏关键词（复合短语，避免"玩"单字误触发）
_GAME_KEYWORDS: tuple[str, ...] = (
    "玩游戏", "来玩游戏", "玩个游戏", "陪我玩", "小游戏", "来一局",
    "猜数字", "石头剪刀布", "剪刀石头布", "快速反应", "反应游戏",
    "play a game", "play game", "let's play",
)

# 音乐关键词
_MUSIC_KEYWORDS: tuple[str, ...] = (
    "推荐首歌", "推荐歌曲", "推荐音乐", "放首歌", "放首音乐", "放音乐",
    "想听歌", "想听音乐", "来首歌", "来点音乐", "听音乐", "放点音乐",
    "歌单", "好听的歌", "music", "song", "play music",
)

# 休息关键词（用户主动提休息 → 弹建议卡）
_REST_KEYWORDS: tuple[str, ...] = (
    "休息一下", "休息会", "休息会儿", "歇会", "歇一会儿", "想休息",
    "好累想休息", "take a break", "rest",
)


class InteractionIntentDetector:
    """从用户消息识别交互意图（游戏优先 → 音乐 → 休息）。"""

    def __init__(self) -> None:
        self._game = tuple(_GAME_KEYWORDS)
        self._music = tuple(_MUSIC_KEYWORDS)
        self._rest = tuple(_REST_KEYWORDS)

    def detect(self, text: str) -> Optional[InteractionIntent]:
        """检测文本意图；空文本 / 无命中返回 None。"""
        if not text or not text.strip():
            return None
        lowered = text.strip().lower()
        for kw in self._game:
            if kw in lowered:
                return InteractionIntent(INTENT_GAME, confidence=1.0, matched=kw)
        for kw in self._music:
            if kw in lowered:
                return InteractionIntent(INTENT_MUSIC, confidence=1.0, matched=kw)
        for kw in self._rest:
            if kw in lowered:
                return InteractionIntent(INTENT_REST, confidence=1.0, matched=kw)
        return None


# ── 卡片构建 ─────────────────────────────────────────────

def build_game_invite_card() -> dict:
    """小游戏邀请卡片（三个游戏入口）。"""
    actions = [
        (GAME_CATALOG["guess_number"]["icon"] + " 猜数字", "open_game:guess_number"),
        (GAME_CATALOG["rps"]["icon"] + " 石头剪刀布", "open_game:rps"),
        (GAME_CATALOG["reaction"]["icon"] + " 快速反应", "open_game:reaction"),
    ]
    return {
        "kind": INTENT_GAME,
        "title": "🎮 小游戏时间！",
        "body": "要不要一起玩个小游戏？赢了有惊喜哦～",
        "actions": actions,
    }


def build_music_card(track: MusicTrack) -> dict:
    """音乐推荐卡片（歌名/歌手 + 理由 + 播放入口）。"""
    artist = f" — {track.artist}" if track.artist else ""
    return {
        "kind": INTENT_MUSIC,
        "title": f"{track.icon} {track.title}{artist}",
        "body": f"推荐理由：{track.reason}",
        "actions": [
            ("▶ 播放", f"music_play:{track.id}"),
            ("📂 打开音乐文件夹", "music_open_folder"),
        ],
    }


def build_rest_card(suggestion: BreakSuggestion, *, threshold_min: float = 90.0,
                    late_night: bool = False) -> dict:
    """休息建议卡片（确认重置计时 / 稍后提醒）。"""
    hint = "（深夜了，小声提醒～）" if late_night else ""
    return {
        "kind": INTENT_REST,
        "title": f"{suggestion.icon} 该休息啦",
        "body": f"你已经连续工作约 {int(threshold_min)} 分钟了，{suggestion.desc}{hint}",
        "actions": [
            ("✅ 好，休息一下", "rest_ack"),
            ("⏳ 再等 10 分钟", "rest_snooze"),
        ],
    }


# ── 事件记录（复用 EventStream / EventBus）────────────────

def record_interaction_event(
    category: str = "",
    scenario: str = "",
    intent: str = "",
    topic: str = "",
    emotion: str = "",
    intensity: float = 0.0,
    source: str = "interaction",
    *,
    record_event: Optional[Callable] = None,
    event_bus_event: str = "",
) -> None:
    """把一次交互行为记入事件日志（EventStream 经 record_event 回调写入）。

    - ``record_event``：兼容 ``CompanionMemory.record_event`` 的可选回调；
      未注入时只走 EventBus（进程内埋点），不落盘（防御式）。
    - ``event_bus_event``：EventBus 事件名（如 mini_game_finished）；
      为空则不发 EventBus。
    """
    try:
        if record_event is not None:
            record_event(
                category=category, scenario=scenario, intent=intent,
                emotion=emotion, intensity=intensity, topic=topic,
                source=source,
            )
    except Exception as exc:
        logger.debug("record_interaction_event -> event_stream 失败: %s", exc)
    if event_bus_event:
        try:
            EventBus.emit(
                event_bus_event,
                category=category, scenario=scenario, intent=intent,
                topic=topic, emotion=emotion, intensity=intensity, source=source,
            )
        except Exception as exc:
            logger.debug("record_interaction_event -> event_bus 失败: %s", exc)


__all__ = [
    "INTENT_GAME",
    "INTENT_MUSIC",
    "INTENT_REST",
    "InteractionIntent",
    "InteractionIntentDetector",
    "VALID_INTENTS",
    "build_game_invite_card",
    "build_music_card",
    "build_rest_card",
    "record_interaction_event",
]
