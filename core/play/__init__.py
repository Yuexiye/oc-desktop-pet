# -*- coding: utf-8 -*-
"""互动层（P2）— 小游戏 / 音乐推荐 / 休息提醒

- games.py                小游戏纯逻辑（猜数字 / 石头剪刀布 / 快速反应）
- music_recommender.py    预设曲库 + 场景/情绪/时段匹配的音乐推荐
- break_reminder.py       联动专注模式的休息提醒状态机
- interaction_dispatcher.py  聊天意图检测 + 卡片构建 + 事件记录
"""
from __future__ import annotations

from core.play.break_reminder import (
    BREAK_SUGGESTIONS,
    DEFAULT_WORK_REMINDER_CONFIG,
    BreakSuggestion,
    WorkReminderTracker,
    is_late_night,
    load_work_reminder_config,
)
from core.play.games import (
    GAME_CATALOG,
    GameResult,
    GuessNumberGame,
    ReactionGame,
    RockPaperScissorsGame,
    create_game,
)
from core.play.interaction_dispatcher import (
    INTENT_GAME,
    INTENT_MUSIC,
    INTENT_REST,
    InteractionIntent,
    InteractionIntentDetector,
    build_game_invite_card,
    build_music_card,
    build_rest_card,
    record_interaction_event,
)
from core.play.music_recommender import (
    DEFAULT_MUSIC_DIR,
    MUSIC_LIBRARY,
    MusicRecommender,
    MusicTrack,
    open_music_folder,
    open_play_target,
    resolve_play_target,
)

__all__ = [
    "BREAK_SUGGESTIONS",
    "DEFAULT_MUSIC_DIR",
    "DEFAULT_WORK_REMINDER_CONFIG",
    "GAME_CATALOG",
    "INTENT_GAME",
    "INTENT_MUSIC",
    "INTENT_REST",
    "MUSIC_LIBRARY",
    "BreakSuggestion",
    "GameResult",
    "GuessNumberGame",
    "InteractionIntent",
    "InteractionIntentDetector",
    "MusicRecommender",
    "MusicTrack",
    "ReactionGame",
    "RockPaperScissorsGame",
    "WorkReminderTracker",
    "build_game_invite_card",
    "build_music_card",
    "build_rest_card",
    "create_game",
    "is_late_night",
    "load_work_reminder_config",
    "open_music_folder",
    "open_play_target",
    "record_interaction_event",
    "resolve_play_target",
]
