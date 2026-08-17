"""场景聚类 — 事件流 → 可回忆单元（纯函数，无新依赖）

把零散事件聚成"场景"：同 category/scenario + 时间连续性合并，
"深夜加班/打某游戏/追剧"各自成一条场景，重复场景计数。

规则：
- 按 ts 排序
- 同类 category/scenario + 相邻事件间隔 ≤ gap_min（分钟）合并
- 合并段累计时长 ≥ min_duration_min 才成场景
- 跨天强制拆分（day_rollover=True）
- 场景标签 tags = [category, scenario, period, emotion_summary]
- emotion_summary 取该场景事件情绪众数（缺省 neutral）

设计要点：
- 纯函数，便于单测；不依赖文件/网络/Qt
- Scene 为 dataclass，序列化由 SceneMemory 负责（asdict）
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# 场景 → 中文标签（未收录回退 scenario / category 原样）
SCENE_LABELS: dict[str, str] = {
    "late_night_work": "深夜加班",
    "long_work_break": "长时间工作",
    "tutorial_follow": "学习教程",
    "window_switch": "频繁切窗",
    "gaming": "打游戏",
    "video_watching": "看视频",
    "chat_idle": "安静发呆",
    "morning_first": "清晨",
    "weekend_play": "周末娱乐",
    "late_night_all": "深夜",
    "development": "写代码",
    "writing": "写作",
    "browsing": "浏览网页",
    "communication": "聊天沟通",
    "entertainment": "娱乐",
    "work": "工作",
    "learn": "学习",
}

# 时段 → 中文标签（按小时）
_PERIOD_LABELS: list[tuple[range, str]] = [
    (range(0, 6), "深夜"),
    (range(6, 9), "清晨"),
    (range(9, 12), "上午"),
    (range(12, 14), "中午"),
    (range(14, 18), "下午"),
    (range(18, 22), "晚上"),
    (range(22, 24), "深夜"),
]

SCENE_TOPICS_MAX = 5       # 场景内话题保留上限（条）
SCENE_TOPIC_CHARS = 60     # 话题截断长度


@dataclass
class Scene:
    """一条可回忆场景。"""
    scene_id: str          # f"{category}|{scenario or label}|{first_date}"
    label: str             # 中文标签："深夜加班" / "打游戏" ...
    category: str
    scenario: str
    tags: list[str]        # [category, scenario, period, emotion_summary]
    first_ts: float
    last_ts: float
    count: int             # 聚合事件数（该场景本次聚类合并的事件条数）
    duration_min: float
    emotion_summary: str   # 众数情绪
    topics: list[str] = field(default_factory=list)   # 该场景内话题（≤5 条，截断）


def _period_of(ts: float) -> str:
    """按小时返回时段中文标签。"""
    try:
        hour = datetime.fromtimestamp(ts).hour
    except Exception:
        return "other"
    for rng, label in _PERIOD_LABELS:
        if hour in rng:
            return label
    return "other"


def _first_date(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def _scene_label(category: str, scenario: str) -> str:
    return SCENE_LABELS.get(scenario) or SCENE_LABELS.get(category) or (scenario or category or "活动")


def _scene_id(category: str, scenario: str, label: str, first_ts: float) -> str:
    key = scenario or label
    return f"{category or 'other'}|{key or 'unknown'}|{_first_date(first_ts)}"


def _emotion_mode(emotions: list[str]) -> str:
    """情绪众数（缺省 neutral）。"""
    if not emotions:
        return "neutral"
    counter = Counter(emotions)
    return counter.most_common(1)[0][0]


def _truncate_topic(topic: str, max_chars: int = SCENE_TOPIC_CHARS) -> str:
    if not topic:
        return ""
    cleaned = " ".join(str(topic).split())
    return cleaned[:max_chars]


def cluster_events(events: list[dict], min_duration_min: int = 20,
                   gap_min: int = 10, day_rollover: bool = True) -> list[Scene]:
    """把事件流聚成场景列表（纯函数）。

    Args:
        events: 事件 dict 列表（EventStream.read_all 的输出）
        min_duration_min: 合并段累计时长 ≥ 该值才成场景（分钟，默认 20）
        gap_min: 同 category/scenario 相邻事件间隔 ≤ 该值才合并（分钟，默认 10）
        day_rollover: 跨天强制拆分（默认 True）

    Returns:
        按 first_ts 升序的 Scene 列表
    """
    if not events:
        return []
    try:
        min_duration_min = max(0, int(min_duration_min))
        gap_min = max(0, int(gap_min))
    except Exception:
        min_duration_min, gap_min = 20, 10

    def _ts(rec: dict) -> float:
        ts = rec.get("ts")
        if ts is None:
            ts = rec.get("end_ts")
        if ts is None:
            ts = rec.get("start_ts")
        try:
            return float(ts or 0.0)
        except Exception:
            return 0.0

    # 过滤无时间/无分类的事件，按 ts 排序
    valid = [e for e in events if isinstance(e, dict) and _ts(e) > 0]
    valid.sort(key=_ts)
    if not valid:
        return []

    # ── 分段合并：同 category/scenario + 间隔 ≤ gap_min + 同一天 ──
    segments: list[list[dict]] = []
    current: list[dict] = []
    current_key: tuple = (None, None)
    current_day: str = ""

    def _same_day(a: float, b: float) -> bool:
        if not day_rollover:
            return True
        return _first_date(a) == _first_date(b)

    for ev in valid:
        ts = _ts(ev)
        category = ev.get("category", "") or ""
        scenario = ev.get("scenario", "") or ""
        key = (category, scenario)
        if (
            current
            and key == current_key
            and (ts - _ts(current[-1])) / 60.0 <= gap_min
            and _same_day(current[-1] and _ts(current[-1]), ts)
        ):
            current.append(ev)
        else:
            if current:
                segments.append(current)
            current = [ev]
            current_key = key
            current_day = _first_date(ts)
    if current:
        segments.append(current)

    # ── 段 → Scene ──
    scenes: list[Scene] = []
    for seg in segments:
        first_ts = _ts(seg[0])
        last_ts = _ts(seg[-1])
        duration_min = max(0.0, (last_ts - first_ts) / 60.0)
        if duration_min < min_duration_min:
            continue
        category = seg[0].get("category", "") or ""
        scenario = seg[0].get("scenario", "") or ""
        label = _scene_label(category, scenario)
        emotions = [str(e.get("emotion") or "neutral") for e in seg if e.get("emotion")]
        emotion = _emotion_mode(emotions)
        period = _period_of(first_ts)
        # 话题：去重、截断、最多 SCENE_TOPICS_MAX 条
        topics: list[str] = []
        seen: set[str] = set()
        for e in seg:
            topic = e.get("topic")
            if not topic:
                continue
            t = _truncate_topic(topic)
            if t and t not in seen:
                seen.add(t)
                topics.append(t)
            if len(topics) >= SCENE_TOPICS_MAX:
                break
        scenes.append(Scene(
            scene_id=_scene_id(category, scenario, label, first_ts),
            label=label,
            category=category,
            scenario=scenario,
            tags=[category, scenario, period, emotion],
            first_ts=first_ts,
            last_ts=last_ts,
            count=len(seg),
            duration_min=round(duration_min, 1),
            emotion_summary=emotion,
            topics=topics,
        ))
    scenes.sort(key=lambda s: s.first_ts)
    return scenes


__all__ = ["Scene", "cluster_events", "SCENE_LABELS"]
