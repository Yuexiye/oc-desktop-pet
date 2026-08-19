"""屏幕/意图感知升级 — 场景分类 + 事件置信度 + LLM 语义增强（P1-6）。

对照 N.E.K.O. ``main_logic/activity/snapshot.py`` 的意图识别逻辑重写
（状态/倾向/口吻词汇表移植自上游，见 ``docs/THIRD_PARTY_NOTICES.md``）：

- ``classify_screen_scene`` 纯规则分类器：把屏幕感知信号（窗口分类/活动/
  时段/持续时长/标题关键词）映射为结构化场景（工作/娱乐/摸鱼/深夜/游戏/
  视频/学习/聊天/私密/空闲），带置信度与理由。
- ``ScreenScene`` 携带 N.E.K.O. 语义的 ``propensity``（closed /
  restricted_screen_only / open / greeting_window）与 ``tone``，供
  proactive 决定"该不该打扰 / 用什么口吻"、focus 决定"该不该给专注分"。
- ``enrich_screen_scene`` 可选 LLM 语义增强（走 Hanako source="screen_enrich"）：
  失败/超时/解析错误一律返回 None → 调用方保留规则结果（**不阻塞感知**）。

联动（复用 P0 已有接口）：
- ``to_intent_scenario``：屏幕场景 → proactive 意图场景名（scenarios.py 词汇）
- ``focus_score_from_scene``：屏幕场景 → ``core.perception.focus.FocusScore``
  （喂给 ``FocusStateMachine.update``，P0-5 接口）

纯函数设计：本模块零 Qt / 零 I/O，可离屏单测。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── 场景词汇表（proactive 场景回忆 / focus 打分共用）──────────────

# 场景名 → (intent, propensity, tone) 的规则映射
# intent 复用 oc-pet intent.py 词汇（work/learn/entertain/tense/tired）+
# 扩展（slacking/chatting/idle/private/gaming/video）
# propensity 复用 N.E.K.O. snapshot.py 语义：
#   closed                  = 私密，硬跳过（proactive 直接不触发）
#   restricted_screen_only  = 只允许屏幕相关闲聊（游戏/深度工作/沉浸视频）
#   open                    = 默认放行
#   greeting_window         = 久别回归，鼓励回忆
# tone 复用 N.E.K.O. ActivityTone（terse/hushed/mellow/playful/witty/warm/concise）
_SCENE_RULE_MAP: dict[str, dict[str, str]] = {
    "private":            {"intent": "private",  "propensity": "closed",                 "tone": "concise"},
    "gaming":             {"intent": "gaming",   "propensity": "restricted_screen_only", "tone": "playful"},
    "video_watching":     {"intent": "video",    "propensity": "restricted_screen_only", "tone": "witty"},
    "music_listening":    {"intent": "entertain","propensity": "open",                   "tone": "mellow"},
    "work_focus":         {"intent": "work",     "propensity": "restricted_screen_only", "tone": "concise"},
    "long_work_break":    {"intent": "tired",    "propensity": "open",                   "tone": "concise"},
    "late_night_work":    {"intent": "tense",    "propensity": "restricted_screen_only", "tone": "concise"},
    "learning":           {"intent": "learn",    "propensity": "restricted_screen_only", "tone": "concise"},
    "chatting":           {"intent": "chatting", "propensity": "open",                   "tone": "warm"},
    "slacking":           {"intent": "slacking", "propensity": "open",                   "tone": "playful"},
    "idle":               {"intent": "idle",     "propensity": "open",                   "tone": "playful"},
    "other":              {"intent": "work",     "propensity": "open",                   "tone": "concise"},
}

# 屏幕场景 → proactive 场景名（scenarios.py 词汇；未列出的用兜底）
_SCENE_TO_INTENT_SCENARIO: dict[str, str] = {
    "private": "chat_idle",
    "gaming": "gaming",
    "video_watching": "video_watching",
    "music_listening": "weekend_play",
    "work_focus": "long_work_break",
    "long_work_break": "long_work_break",
    "late_night_work": "late_night_work",
    "learning": "tutorial_follow",
    "chatting": "chat_idle",
    "slacking": "weekend_play",
    "idle": "chat_idle",
    "other": "chat_idle",
}

# 屏幕场景 → focus 打分（score；None 键 = 无证据，不喂）
# 正分 = 深度专注证据；0 = 中性；负分 = 非专注投票
_SCENE_TO_FOCUS_SCORE: dict[str, float] = {
    "private": 0.0,
    "gaming": -0.4,
    "video_watching": -0.3,
    "music_listening": -0.1,
    "work_focus": 0.7,
    "long_work_break": 0.4,
    "late_night_work": 0.8,
    "learning": 0.6,
    "chatting": 0.1,
    "slacking": -0.3,
    "idle": 0.0,
    "other": 0.0,
}

# 深夜时段（与 intent.py LATE_NIGHT_PERIODS 对齐）
LATE_NIGHT_PERIODS = {"late_night", "midnight"}

# 工作时间段（摸鱼判定：工作日 9-18 点做休闲类）
WORK_HOUR_START = 9
WORK_HOUR_END = 18

# 关键词规则：标题/描述命中 → 场景（覆盖通用分类）
TITLE_SCENE_KEYWORDS: dict[str, tuple[str, float]] = {
    # 游戏
    "steam": ("gaming", 0.95), "epic games": ("gaming", 0.95),
    "lol": ("gaming", 0.95), "league of legends": ("gaming", 0.95),
    "英雄联盟": ("gaming", 0.95), "王者荣耀": ("gaming", 0.95),
    "minecraft": ("gaming", 0.95), "我的世界": ("gaming", 0.95),
    "genshin": ("gaming", 0.95), "原神": ("gaming", 0.95),
    "valorant": ("gaming", 0.95), "cs2": ("gaming", 0.95),
    "dota": ("gaming", 0.95), "apex": ("gaming", 0.95),
    "游戏": ("gaming", 0.85),
    # 视频/直播
    "bilibili": ("video_watching", 0.9), "哔哩哔哩": ("video_watching", 0.9),
    "youtube": ("video_watching", 0.9), "爱奇艺": ("video_watching", 0.9),
    "腾讯视频": ("video_watching", 0.9), "优酷": ("video_watching", 0.9),
    "netflix": ("video_watching", 0.9), "twitch": ("video_watching", 0.9),
    "直播": ("video_watching", 0.85), "电影": ("video_watching", 0.8),
    # 音乐
    "spotify": ("music_listening", 0.9), "网易云音乐": ("music_listening", 0.9),
    "qq音乐": ("music_listening", 0.9), "酷狗音乐": ("music_listening", 0.9),
    # 聊天/通讯
    "wechat": ("chatting", 0.85), "微信": ("chatting", 0.85),
    "qq": ("chatting", 0.75), "钉钉": ("chatting", 0.75),
    "slack": ("chatting", 0.8), "discord": ("chatting", 0.8),
    "teams": ("chatting", 0.8), "飞书": ("chatting", 0.75),
    "outlook": ("chatting", 0.7), "邮件": ("chatting", 0.7),
    # IDE / 文档（工作）
    "visual studio": ("work_focus", 0.9), "vscode": ("work_focus", 0.9),
    "code": ("work_focus", 0.8), "pycharm": ("work_focus", 0.9),
    "intellij": ("work_focus", 0.9), "webstorm": ("work_focus", 0.9),
    "android studio": ("work_focus", 0.9), "xcode": ("work_focus", 0.9),
    "sublime": ("work_focus", 0.85), "notepad": ("work_focus", 0.75),
    "terminal": ("work_focus", 0.85), "终端": ("work_focus", 0.85),
    "powershell": ("work_focus", 0.85), "cmd": ("work_focus", 0.7),
    "word": ("work_focus", 0.8), "excel": ("work_focus", 0.8),
    "powerpoint": ("work_focus", 0.8), "wps": ("work_focus", 0.8),
    "office": ("work_focus", 0.8), "文档": ("work_focus", 0.7),
    "浏览器": ("browsing", 0.6),
    # 学习
    "coursera": ("learning", 0.9), "udemy": ("learning", 0.9),
    "khan academy": ("learning", 0.9), "知乎": ("learning", 0.7),
    "教程": ("learning", 0.75), "课程": ("learning", 0.75),
    "学习": ("learning", 0.7),
    # 私密
    "password": ("private", 0.98), "密码": ("private", 0.98),
    "银行": ("private", 0.95), "bank": ("private", 0.95),
    "支付": ("private", 0.9), "private key": ("private", 0.98),
}

# 描述/摘要里出现这些词 → 摸鱼（休闲浏览）
_SLACK_TEXT_KEYWORDS: tuple[str, ...] = (
    "摸鱼", "划水", "摸鱼中", "偷懒", "刷手机", "刷视频",
    "shopping", "淘宝", "京东", "拼多多", "购物", "八卦", "热搜",
)

# 场景名 → 中文标签（展示/日志）
SCENE_LABELS: dict[str, str] = {
    "private": "私密处理",
    "gaming": "游戏",
    "video_watching": "看视频",
    "music_listening": "听音乐",
    "work_focus": "工作专注",
    "long_work_break": "长时间工作",
    "late_night_work": "深夜加班",
    "learning": "学习",
    "chatting": "聊天通讯",
    "slacking": "摸鱼",
    "idle": "空闲",
    "other": "其他",
}

VALID_SCENES: frozenset = frozenset(_SCENE_RULE_MAP.keys())
# LLM 允许返回的场景集合（不信任任意字符串）
LLM_ALLOWED_SCENES: frozenset = VALID_SCENES - {"private"}


# ── ScreenScene ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ScreenScene:
    """屏幕感知的结构化场景结果（不可变，按值传递）。

    字段语义对照 N.E.K.O. ``ActivitySnapshot``：
    - ``propensity``：proactive 打扰门槛（closed=硬跳过）
    - ``tone``：口吻提示（terse/hushed/mellow/playful/witty/warm/concise）
    - ``llm_guess``：LLM 增强的一句话叙述（可选）
    """

    scene: str = "other"
    intent: str = "work"
    confidence: float = 0.5
    propensity: str = "open"
    tone: str = "concise"
    reasons: tuple[str, ...] = ()
    source: str = "rule"          # "rule" | "llm" | "hybrid"
    llm_guess: str = ""
    category: str = "other"       # 原始窗口分类（work/learn/entertainment/...）
    activity: str = "idle"        # 原始活动状态（typing/mouse/idle）
    period: str = "other"         # 原始时段

    def to_dict(self) -> dict:
        return {
            "scene": self.scene,
            "intent": self.intent,
            "confidence": round(float(self.confidence), 3),
            "propensity": self.propensity,
            "tone": self.tone,
            "reasons": list(self.reasons),
            "source": self.source,
            "llm_guess": self.llm_guess,
            "category": self.category,
            "activity": self.activity,
            "period": self.period,
        }


def _scene_meta(scene: str) -> dict[str, str]:
    """查场景的 (intent, propensity, tone) 元数据；未知场景用 other 兜底。"""
    return _SCENE_RULE_MAP.get(scene, _SCENE_RULE_MAP["other"])


def _normalize_category(category: str) -> str:
    """把 oc-pet 两套窗口分类（foreground: writing/development/gaming/...
    与 vision: work/learn/entertainment/communication/other）统一到意图空间。"""
    cat = (category or "").strip().lower()
    mapping = {
        "writing": "work", "development": "work", "code": "work",
        "work": "work", "office": "work",
        "browsing": "browsing",
        "gaming": "gaming", "game": "gaming",
        "entertainment": "entertainment", "video": "entertainment",
        "learn": "learning", "learning": "learning", "study": "learning",
        "communication": "communication", "chat": "communication",
        "private": "private",
    }
    return mapping.get(cat, "other")


def _match_title_keywords(app: str, title: str) -> tuple[str, float] | None:
    """窗口标题/进程名关键词匹配（小写包含）。命中返回 (scene, confidence)。"""
    haystack = " ".join([app or "", title or ""]).lower()
    if not haystack:
        return None
    best: tuple[str, float] | None = None
    for keyword, (scene, conf) in TITLE_SCENE_KEYWORDS.items():
        if keyword.lower() in haystack:
            if best is None or conf > best[1]:
                best = (scene, conf)
    return best


def _detect_slacking(
    category: str,
    period: str,
    hour: int,
    is_weekend: bool,
    text: str,
) -> bool:
    """摸鱼判定：工作日工作时段（9-18 点）做休闲类，或描述里出现摸鱼词。"""
    if text:
        lowered = (text or "").lower()
        for kw in _SLACK_TEXT_KEYWORDS:
            if kw in lowered:
                return True
    if is_weekend:
        return False
    if WORK_HOUR_START <= hour < WORK_HOUR_END:
        return category in ("browsing", "entertainment")
    return False


def classify_screen_scene(
    *,
    category: str = "other",
    activity: str = "idle",
    period: str = "other",
    hour: int = 12,
    weekday: int = 0,
    is_weekend: bool = False,
    fg_duration_min: float = 0.0,
    app: str = "",
    title: str = "",
    description: str = "",
    detail: str = "",
) -> ScreenScene:
    """纯规则场景分类（P1-6 核心；可离屏单测）。

    Args:
        category: 窗口分类（foreground 或 vision 词汇均可）。
        activity: typing / mouse / idle。
        period: morning/afternoon/evening/night/late_night/midnight。
        hour: 0-23 本地小时。
        weekday: 0=周一。
        is_weekend: 是否周末。
        fg_duration_min: 当前分类持续分钟数。
        app / title: 前台进程名 / 窗口标题（关键词匹配用）。
        description / detail: 视觉模型描述（摸鱼词 / 学习词匹配用）。

    Returns:
        ScreenScene（永不抛异常；未知输入回退 other）。
    """
    reasons: list[str] = []
    norm_cat = _normalize_category(category)
    late_night = period in LATE_NIGHT_PERIODS
    text = " ".join([description or "", detail or ""])

    # 1) 私密（关键词最高优先）
    kw_hit = _match_title_keywords(app, title)
    if kw_hit and kw_hit[0] == "private":
        meta = _scene_meta("private")
        return ScreenScene(
            scene="private", intent=meta["intent"], confidence=kw_hit[1],
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("title_private",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 2) 标题/进程关键词（游戏/视频/音乐/聊天/IDE 等）
    if kw_hit:
        scene, conf = kw_hit
        meta = _scene_meta(scene)
        # 深夜 + 工作关键词 → 升级为深夜加班
        if late_night and scene == "work_focus" and fg_duration_min >= 30:
            scene = "late_night_work"
            meta = _scene_meta(scene)
            conf = min(0.95, conf + 0.1)
            reasons.append("late_night_work")
        return ScreenScene(
            scene=scene, intent=meta["intent"], confidence=conf,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=tuple(reasons) + ("title_keyword",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 3) 深夜加班：深夜 + 工作/学习 + 持续 30 分钟以上
    if late_night and norm_cat in ("work", "learning", "browsing") and fg_duration_min >= 30:
        scene = "late_night_work"
        meta = _scene_meta(scene)
        conf = min(0.95, 0.7 + fg_duration_min / 200)
        return ScreenScene(
            scene=scene, intent=meta["intent"], confidence=conf,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("late_night_work",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 4) 游戏
    if norm_cat == "gaming":
        meta = _scene_meta("gaming")
        return ScreenScene(
            scene="gaming", intent=meta["intent"], confidence=0.9,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("category_gaming",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 5) 娱乐/看视频
    if norm_cat == "entertainment":
        meta = _scene_meta("video_watching")
        return ScreenScene(
            scene="video_watching", intent=meta["intent"], confidence=0.85,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("category_entertainment",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 6) 长时间工作该休息（工作类持续 ≥90 分钟）
    if norm_cat == "work" and fg_duration_min >= 90:
        meta = _scene_meta("long_work_break")
        conf = min(0.9, 0.65 + fg_duration_min / 300)
        return ScreenScene(
            scene="long_work_break", intent=meta["intent"], confidence=conf,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("long_work_break",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 7) 工作专注（工作类 + 打字/鼠标，或 IDE 已由关键词覆盖）
    if norm_cat == "work":
        meta = _scene_meta("work_focus")
        conf = 0.7 if activity == "typing" else 0.6
        return ScreenScene(
            scene="work_focus", intent=meta["intent"], confidence=conf,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("category_work",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 8) 学习（browsing/learning 持续 ≥15 分钟）
    if norm_cat in ("learning", "browsing"):
        if fg_duration_min >= 15 or norm_cat == "learning":
            meta = _scene_meta("learning")
            conf = min(0.85, 0.6 + fg_duration_min / 120)
            return ScreenScene(
                scene="learning", intent=meta["intent"], confidence=conf,
                propensity=meta["propensity"], tone=meta["tone"],
                reasons=("category_learning",),
                source="rule", category=category, activity=activity, period=period,
            )
        # 浏览未达学习阈值：工作时段 → 摸鱼；其他 → 空闲/娱乐
        if _detect_slacking(norm_cat, period, hour, is_weekend, text):
            meta = _scene_meta("slacking")
            return ScreenScene(
                scene="slacking", intent=meta["intent"], confidence=0.65,
                propensity=meta["propensity"], tone=meta["tone"],
                reasons=("slacking_browsing",),
                source="rule", category=category, activity=activity, period=period,
            )

    # 9) 通讯/聊天
    if norm_cat == "communication":
        meta = _scene_meta("chatting")
        return ScreenScene(
            scene="chatting", intent=meta["intent"], confidence=0.75,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("category_communication",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 10) 摸鱼文本词
    if _detect_slacking(norm_cat, period, hour, is_weekend, text):
        meta = _scene_meta("slacking")
        return ScreenScene(
            scene="slacking", intent=meta["intent"], confidence=0.7,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("slacking_text",),
            source="rule", category=category, activity=activity, period=period,
        )

    # 11) 空闲/其他
    if activity == "idle" and norm_cat == "other":
        meta = _scene_meta("idle")
        return ScreenScene(
            scene="idle", intent=meta["intent"], confidence=0.5,
            propensity=meta["propensity"], tone=meta["tone"],
            reasons=("idle_other",),
            source="rule", category=category, activity=activity, period=period,
        )

    meta = _scene_meta("other")
    return ScreenScene(
        scene="other", intent=meta["intent"], confidence=0.4,
        propensity=meta["propensity"], tone=meta["tone"],
        reasons=("fallback",),
        source="rule", category=category, activity=activity, period=period,
    )


# ── LLM 语义增强（可选；失败回退规则，不阻塞感知）──────────────

SCREEN_ENRICH_PROMPT = """你是桌宠的屏幕意图标注器。根据以下屏幕感知信号，判断用户当前在做什么场景，以 JSON 返回。

屏幕信号：
- 窗口进程：{app}
- 窗口标题：{title}
- 前台分类：{category}
- 活动状态：{activity}
- 时段：{period}
- 持续时长：{duration_min} 分钟
- 视觉摘要：{summary}
- 视觉详情：{detail}
- 规则初步判断：{rule_scene}

返回格式（只返回 JSON）：
{{
  "scene": "gaming / video_watching / music_listening / work_focus / long_work_break / late_night_work / learning / chatting / slacking / idle / other 之一",
  "confidence": 0.0到1.0,
  "guess": "一句话叙述用户在做什么（20-40字，中文）"
}}

规则：
- 永远不要提及密码、验证码、密钥、token、银行账户等敏感信息；检测到敏感内容返回 scene=other，confidence=0.9
- 规则初步判断与你的观察矛盾时，以更合理的为准，并在 guess 里说明
- 只返回 JSON，不要其他文字"""


def build_screen_enrich_prompt(scene: ScreenScene, **extra: Any) -> str:
    """构造屏幕语义增强的 LLM 指令（参考 N.E.K.O. llm_enrichment 思路重写）。"""
    return SCREEN_ENRICH_PROMPT.format(
        app=str(extra.get("app") or "")[:80],
        title=str(extra.get("title") or "")[:120],
        category=scene.category or "other",
        activity=scene.activity or "idle",
        period=scene.period or "other",
        duration_min=int(float(extra.get("fg_duration_min") or 0)),
        summary=str(extra.get("summary") or "")[:120],
        detail=str(extra.get("detail") or "")[:200],
        rule_scene=scene.scene,
    )


def _extract_json_object(text: str) -> dict | None:
    """从可能夹带文字的回复中提取第一个完整 JSON 对象（括号配对扫描）。"""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    import json
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def _sanitize_llm_scene(raw: dict, rule_scene: ScreenScene) -> ScreenScene | None:
    """把 LLM 返回的 dict 清洗为 ScreenScene；非法/缺字段返回 None（调用方保留规则）。"""
    try:
        scene = str(raw.get("scene") or "").strip()
        if scene not in LLM_ALLOWED_SCENES:
            return None
        conf = float(raw.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
        guess = str(raw.get("guess") or "").strip()[:80]
    except (TypeError, ValueError):
        return None
    meta = _scene_meta(scene)
    return ScreenScene(
        scene=scene,
        intent=meta["intent"],
        confidence=conf,
        propensity=meta["propensity"],
        tone=meta["tone"],
        reasons=("llm_enrich",),
        source="hybrid",
        llm_guess=guess,
        category=rule_scene.category,
        activity=rule_scene.activity,
        period=rule_scene.period,
    )


def enrich_screen_scene(
    rule_scene: ScreenScene,
    provider: Callable[[str], str | None] | None,
    **extra: Any,
) -> ScreenScene:
    """可选 LLM 语义增强：成功返回合并结果，失败/超时/解析错误返回规则结果。

    Args:
        rule_scene: 规则分类结果（永不丢失——失败回退它）。
        provider: 同步 LLM 提供函数 ``fn(prompt: str) -> str | None``。
            None → 直接返回规则结果（未配置增强）。
        extra: 附加信号（app/title/summary/detail/fg_duration_min），
            传给 prompt 构造。

    Returns:
        ScreenScene：LLM 成功 = source="hybrid"（规则兜底保证非 None）；
        失败 = 原样返回 rule_scene（source="rule"）。
    """
    if provider is None:
        return rule_scene
    try:
        prompt = build_screen_enrich_prompt(rule_scene, **extra)
        raw_text = provider(prompt)
        if not raw_text or not raw_text.strip():
            return rule_scene
        parsed = _extract_json_object(raw_text)
        if parsed is None:
            return rule_scene
        merged = _sanitize_llm_scene(parsed, rule_scene)
        if merged is None:
            return rule_scene
        logger.debug("Screen enrich: %s (conf=%.2f) guess=%s", merged.scene, merged.confidence, merged.llm_guess)
        return merged
    except Exception as exc:
        logger.debug("Screen enrich failed, fallback to rule: %s", exc)
        return rule_scene


# ── 联动适配（复用 P0 已有接口）─────────────────────────────

def to_intent_scenario(scene: str) -> str:
    """屏幕场景 → proactive 意图场景名（scenarios.py 词汇）。"""
    return _SCENE_TO_INTENT_SCENARIO.get(scene, "chat_idle")


def focus_score_from_scene(scene: ScreenScene, scorer=None):
    """屏幕场景 → ``core.perception.focus.FocusScore``（P0-5 接口）。

    供行为层把屏幕意图喂给 ``FocusStateMachine.update(score)``：
    - 深度工作/学习/深夜加班 → 正分（专注证据）
    - 娱乐/摸鱼/游戏/视频 → 负分（非专注投票）
    - 空闲/其他 → 0 分（无证据）

    Args:
        scene: ScreenScene 或任意带 ``scene`` 属性的对象；None → None。
        scorer: 可选 FocusScorer 实例（仅用于构造 FocusScore 的兼容；默认为空）。

    Returns:
        FocusScore | None（scene 为空/无映射返回 None = 不喂状态机）。
    """
    if scene is None:
        return None
    scene_name = getattr(scene, "scene", "") or ""
    score = _SCENE_TO_FOCUS_SCORE.get(scene_name)
    if score is None:
        return None
    # 延迟导入避免循环依赖（focus.py 无 core.anti_repeat 依赖）
    from core.perception.focus import FocusScore
    return FocusScore(score=float(score), signals={"screen_scene": float(score)})


__all__ = [
    "ScreenScene",
    "classify_screen_scene",
    "enrich_screen_scene",
    "build_screen_enrich_prompt",
    "to_intent_scenario",
    "focus_score_from_scene",
    "SCREEN_ENRICH_PROMPT",
    "SCENE_LABELS",
    "VALID_SCENES",
]
