# -*- coding: utf-8 -*-
"""音乐推荐 — 预设曲库 + 场景/情绪匹配（P2-4）

约束：**不引入外部音乐 API**（无 key），用内置曲库 + 场景匹配：
- ``MusicTrack``：id / 标题 / 歌手 / 标签 / 推荐理由 / 可选播放目标
- ``MusicRecommender.recommend(context)``：按 场景(scene)+情绪(emotion)+时段(hour)
  打分，最高分优先，同分随机；支持 exclude 避免刚推过又推。
- ``resolve_play_target``：播放目标解析（本地文件 / URL / 音乐文件夹兜底）
- ``open_play_target``：系统默认播放器打开（os.startfile / webbrowser）

播放能力说明（oc-pet 现状）：
- 桌宠本身无音乐播放器（QMediaPlayer 仅用于 TTS 语音）。
- 因此"播放"= 用系统默认播放器打开本地音乐文件夹（或 track 自带的
  本地文件 / URL），不引入任何新依赖。
"""
from __future__ import annotations

import logging
import random
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MUSIC_DIR = Path.home() / "Music"

# 场景标签（与 perception/scenarios.py / intent.py 场景名对齐，宽松匹配）
SCENE_TAGS = {
    "work": {"工作", "学习", "专注"},
    "study": {"工作", "学习", "专注"},
    "deep_work": {"工作", "专注"},
    "late_night_work": {"深夜", "工作"},
    "long_work_break": {"放松", "工作"},
    "gaming": {"游戏", "活力"},
    "entertainment": {"放松", "快乐"},
    "chat_idle": {"放松"},
    "rest": {"放松"},
    "break": {"放松"},
    "tired": {"放松", "治愈"},
    "sad": {"治愈", "安静"},
    "happy": {"快乐", "活力"},
}

# 情绪 → 曲库标签（宽松）
EMOTION_TAGS = {
    "happy": {"快乐", "活力"},
    "cute": {"快乐"},
    "surprised": {"活力"},
    "sad": {"治愈", "安静"},
    "angry": {"冷静"},
    "thinking": {"专注", "安静"},
    "missing": {"治愈"},
    "working": {"工作", "专注"},
    "listening": {"安静"},
    "speaking": {"快乐"},
    "neutral": set(),
}

# 时段 → 曲库标签（hour 0-23）
HOUR_TAGS = {
    "morning": {"活力", "快乐"},   # 6-11
    "afternoon": {"工作", "专注"},  # 12-17
    "evening": {"放松"},            # 18-21
    "night": {"安静", "治愈", "深夜"},  # 22-5
}


@dataclass
class MusicTrack:
    """一首预设曲目。``play_target`` 可为本地文件/URL/空（空=音乐文件夹兜底）。"""

    id: str
    title: str
    artist: str = ""
    tags: set = field(default_factory=set)
    reason: str = ""
    play_target: str = ""
    icon: str = "🎵"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "tags": sorted(self.tags),
            "reason": self.reason,
            "play_target": self.play_target,
            "icon": self.icon,
        }


# ── 预设曲库（无版权 API，全部为"理由 + 本地音乐目录/URL"指引）──
MUSIC_LIBRARY: list[MusicTrack] = [
    MusicTrack(
        id="lo-fi_focus", title="Lo-Fi 专注白噪音", artist="(本地收藏)",
        tags={"工作", "专注", "安静"},
        reason="低频节奏 + 咖啡店环境音，写代码/赶工时的专注背景乐",
    ),
    MusicTrack(
        id="rain_piano", title="雨声钢琴曲", artist="(本地收藏)",
        tags={"治愈", "安静", "深夜"},
        reason="雨天窗边钢琴，适合深夜加班后的情绪安抚",
    ),
    MusicTrack(
        id="city_pop", title="City Pop 午后精选", artist="(本地收藏)",
        tags={"快乐", "活力", "放松"},
        reason="轻快复古的 City Pop，午后犯困时来一首提提神",
    ),
    MusicTrack(
        id="classical_piano", title="古典钢琴小品", artist="(本地收藏)",
        tags={"专注", "工作", "安静"},
        reason="巴赫/肖邦的轻古典，需要集中注意力的学习时段很配",
    ),
    MusicTrack(
        id="epic_game_bgm", title="游戏战斗 BGM 燃向", artist="(本地收藏)",
        tags={"游戏", "活力"},
        reason="打游戏、写方案需要燃一把的时候，配这段最带感",
    ),
    MusicTrack(
        id="chill_hop", title="Chill-Hop 慵懒午后", artist="(本地收藏)",
        tags={"放松", "快乐"},
        reason="鼓点松软、旋律慵懒，休息时间躺着听正好",
    ),
    MusicTrack(
        id="night_jazz", title="午夜爵士", artist="(本地收藏)",
        tags={"深夜", "治愈", "放松"},
        reason="萨克斯 + 低音贝斯，深夜一个人的安静陪伴",
    ),
    MusicTrack(
        id="acoustic_folk", title="木吉他民谣", artist="(本地收藏)",
        tags={"治愈", "放松", "安静"},
        reason="简单温暖的和弦，心情低落时像有人在旁边轻轻唱歌",
    ),
    MusicTrack(
        id="synthwave", title="Synthwave 复古合成器", artist="(本地收藏)",
        tags={"活力", "游戏", "深夜"},
        reason="霓虹灯色的合成器音墙，写代码也能写出赛博感",
    ),
    MusicTrack(
        id="calm_meditation", title="冥想呼吸引导曲", artist="(本地收藏)",
        tags={"放松", "治愈", "安静"},
        reason="长音 + 呼吸引导，累了就放空两分钟",
    ),
    MusicTrack(
        id="morning_acoustic", title="元气晨间歌单", artist="(本地收藏)",
        tags={"快乐", "活力"},
        reason="清晨第一首歌就该元气满满，开启一天好心情",
    ),
    MusicTrack(
        id="focus_white_noise", title="森林白噪音", artist="(本地收藏)",
        tags={"工作", "专注", "安静"},
        reason="鸟鸣溪流，把注意力从噪音里捞回来",
    ),
]


def _hour_bucket(hour: Optional[int]) -> str:
    """把小时归到时段桶（morning/afternoon/evening/night）。"""
    try:
        h = int(hour)
    except (TypeError, ValueError):
        h = datetime.now().hour
    if 6 <= h <= 11:
        return "morning"
    if 12 <= h <= 17:
        return "afternoon"
    if 18 <= h <= 21:
        return "evening"
    return "night"


class MusicRecommender:
    """预设曲库推荐器：场景/情绪/时段标签打分，最高分优先、同分随机。"""

    def __init__(self, library: Optional[list[MusicTrack]] = None,
                 seed: Optional[int] = None) -> None:
        self._library: list[MusicTrack] = list(library or MUSIC_LIBRARY)
        self._rng = random.Random(seed)

    @property
    def library(self) -> list[MusicTrack]:
        return list(self._library)

    def get(self, track_id: str) -> Optional[MusicTrack]:
        for t in self._library:
            if t.id == track_id:
                return t
        return None

    def _score(self, track: MusicTrack, scene: str, emotion: str, hour: Optional[int]) -> int:
        score = 0
        tags = track.tags or set()
        scene_tags = SCENE_TAGS.get((scene or "").strip().lower(), set())
        emotion_tags = EMOTION_TAGS.get((emotion or "").strip().lower(), set())
        hour_tags = HOUR_TAGS.get(_hour_bucket(hour), set())
        if scene_tags & tags:
            score += 2
        if emotion_tags & tags:
            score += 2
        if hour_tags & tags:
            score += 1
        return score

    def recommend(self, scene: str = "", emotion: str = "",
                  hour: Optional[int] = None,
                  exclude_ids: Optional[list[str]] = None) -> Optional[MusicTrack]:
        """按上下文推荐一首；无候选返回 None。"""
        exclude = set(exclude_ids or [])
        scored = []
        for track in self._library:
            if track.id in exclude:
                continue
            scored.append((self._score(track, scene, emotion, hour), track))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0]
        best = [t for s, t in scored if s == top]
        return self._rng.choice(best) if best else None

    def pick_for_context(self, context: Optional[dict] = None,
                         exclude_ids: Optional[list[str]] = None) -> Optional[MusicTrack]:
        """从上下文字典（scene/emotion/hour）推荐。"""
        context = context or {}
        return self.recommend(
            scene=str(context.get("scene") or ""),
            emotion=str(context.get("emotion") or ""),
            hour=context.get("hour"),
            exclude_ids=exclude_ids,
        )


# ── 播放目标解析 / 打开 ─────────────────────────────────

def resolve_play_target(track: Optional[MusicTrack],
                        music_dir: Optional[str | Path] = None) -> tuple[str, str]:
    """解析播放目标。

    Returns:
        (kind, target)：kind ∈ {"file", "url", "folder"}；folder 为音乐目录兜底。
    """
    if track is None:
        folder = Path(music_dir) if music_dir else DEFAULT_MUSIC_DIR
        return "folder", str(folder)
    target = (track.play_target or "").strip()
    if target:
        if target.startswith(("http://", "https://")):
            return "url", target
        p = Path(target)
        if p.exists():
            return "file", str(p)
        return "folder", str(Path(music_dir) if music_dir else DEFAULT_MUSIC_DIR)
    folder = Path(music_dir) if music_dir else DEFAULT_MUSIC_DIR
    return "folder", str(folder)


def open_play_target(target: str, kind: str = "") -> bool:
    """用系统默认播放器打开目标（file/url/folder）。失败返回 False（不抛）。"""
    try:
        if kind == "url":
            webbrowser.open(target)
            return True
        if kind == "file":
            _os_startfile(target)
            return True
        if kind == "folder":
            _os_startfile(target)
            return True
        # 未指定 kind：按内容探测
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        else:
            _os_startfile(target)
        return True
    except Exception as exc:
        logger.warning("open_play_target failed: %s", exc)
        return False


def _os_startfile(path: str) -> None:
    """Windows 用 os.startfile；非 Windows 退化为 os.system('start'/open)。"""
    import os
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys_platform_darwin():
        import subprocess
        subprocess.Popen(["open", path])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


def sys_platform_darwin() -> bool:
    import sys
    return sys.platform == "darwin"


def open_music_folder(music_dir: Optional[str | Path] = None) -> bool:
    """打开本地音乐目录（播放能力兜底）。"""
    folder = Path(music_dir) if music_dir else DEFAULT_MUSIC_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return open_play_target(str(folder), kind="folder")


__all__ = [
    "DEFAULT_MUSIC_DIR",
    "EMOTION_TAGS",
    "HOUR_TAGS",
    "MUSIC_LIBRARY",
    "MusicRecommender",
    "MusicTrack",
    "SCENE_TAGS",
    "open_music_folder",
    "open_play_target",
    "resolve_play_target",
]
