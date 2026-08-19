"""记忆卡片 — 事件 / 场景 / 事实三类（P0-8）

数据来自 ``~/.oc-pet/memory/`` 真实文件（T03/T02 落盘的字段），由
``ui.memory_panel.MemoryPanel`` 负责读取并构造；本模块只负责单张卡片
的渲染与三类字段的展示映射。

设计语言沿用 N.E.K.O. 聊天面板 token（``ui/theme/neko.qss``）：
- 左侧 4px 强调色条（event 蓝 / scene 紫 / fact 绿）
- 标题 + 时间行、正文（word wrap）、meta / tags 行
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

KIND_EVENT = "event"
KIND_SCENE = "scene"
KIND_FACT = "fact"
KIND_REFLECTION = "reflection"
KIND_LABELS = {
    KIND_EVENT: "事件",
    KIND_SCENE: "场景",
    KIND_FACT: "事实",
    KIND_REFLECTION: "反思",
}

CATEGORY_LABELS = {
    "development": "开发",
    "writing": "写作",
    "work": "工作",
    "learn": "学习",
    "browsing": "浏览",
    "gaming": "游戏",
    "entertainment": "娱乐",
    "video_watching": "看视频",
    "communication": "聊天",
    "chat_idle": "闲聊",
    "other": "其他",
    "": "未分类",
}


def format_ts(ts: float | None) -> str:
    """时间戳 → "MM-DD HH:MM"（0/None → 空串）。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(str(category or ""), str(category or "未分类"))


class MemoryCard(QFrame):
    """单张记忆卡片。

    动态属性 ``card-kind``（event/scene/fact）与 ``data-theme`` 供
    neko.qss 选取强调色 / 明暗样式。
    """

    def __init__(self, kind: str, title: str = "", text: str = "",
                 time_text: str = "", meta: str = "", tags: str = "",
                 theme: str = "light", parent: QWidget | None = None):
        super().__init__(parent)
        kind = kind if kind in KIND_LABELS else KIND_EVENT
        self._kind = kind
        self._theme = theme if theme in ("light", "dark") else "light"

        self.setObjectName("memoryCard")
        self.setProperty("card-kind", kind)
        self.setProperty("data-theme", self._theme)
        self._title = title or KIND_LABELS[kind]
        self._text = text or ""
        self._time_text = time_text or ""
        self._meta = meta or ""
        self._tags = tags or ""

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 12, 10)
        root.setSpacing(10)

        # 左侧强调色条
        accent = QFrame(self)
        accent.setObjectName("cardAccent")
        accent.setFixedWidth(4)
        root.addWidget(accent)

        # 内容
        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        # 头部行：类型徽标 + 标题 + 时间
        head = QHBoxLayout()
        head.setSpacing(8)
        kind_label = QLabel(KIND_LABELS[kind], body)
        kind_label.setObjectName("cardKind")
        title_label = QLabel(title or KIND_LABELS[kind], body)
        title_label.setObjectName("cardTitle")
        time_label = QLabel(time_text, body)
        time_label.setObjectName("cardTime")
        head.addWidget(kind_label)
        head.addWidget(title_label, stretch=1)
        head.addWidget(time_label)
        body_layout.addLayout(head)

        # 正文
        text_label = QLabel(text, body)
        text_label.setObjectName("cardText")
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body_layout.addWidget(text_label)

        # meta / tags
        if meta or tags:
            foot = QLabel(body)
            foot.setObjectName("cardMeta")
            foot.setText(" · ".join(x for x in (meta, tags) if x))
            foot.setWordWrap(True)
            body_layout.addWidget(foot)

        root.addWidget(body, stretch=1)

        self._time_label = time_label

    # ── 主题 ──

    def set_theme(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            return
        if theme == self._theme:
            return
        self._theme = theme
        self.setProperty("data-theme", theme)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def title(self) -> str:
        return self._title

    @property
    def text(self) -> str:
        return self._text

    @property
    def time_text(self) -> str:
        return self._time_text

    @property
    def meta(self) -> str:
        return self._meta

    @property
    def tags(self) -> str:
        return self._tags

    @property
    def theme(self) -> str:
        return self._theme

    # ── 工厂方法（真实记忆字段 → 卡片） ──

    @classmethod
    def event_card(cls, event: dict, theme: str = "light",
                   parent: QWidget | None = None) -> "MemoryCard":
        """事件流一行 → 事件卡片。字段：ts/category/scenario/intent/emotion/intensity/topic/source。"""
        title = _category_label(event.get("category"))
        scenario = str(event.get("scenario") or "")
        topic = str(event.get("topic") or "")
        text = topic or scenario or "（无文本记录）"
        meta_parts = []
        source = str(event.get("source") or "")
        if source:
            meta_parts.append(f"来源:{source}")
        emotion = str(event.get("emotion") or "")
        if emotion and emotion != "neutral":
            meta_parts.append(f"情绪:{emotion}")
        intent = str(event.get("intent") or "")
        if intent:
            meta_parts.append(f"意图:{intent}")
        return cls(
            KIND_EVENT,
            title=title,
            text=text,
            time_text=format_ts(event.get("ts")),
            meta=" · ".join(meta_parts),
            theme=theme,
            parent=parent,
        )

    @classmethod
    def scene_card(cls, scene: dict, theme: str = "light",
                   parent: QWidget | None = None) -> "MemoryCard":
        """场景表一行 → 场景卡片。字段：label/category/scenario/tags/count/duration_min/emotion_summary/topics。"""
        title = str(scene.get("label") or scene.get("scenario") or _category_label(scene.get("category")))
        topics = list(scene.get("topics") or [])
        text = "、".join(str(t) for t in topics[:4]) or str(scene.get("emotion_summary") or "日常片段")
        meta_parts = []
        count = int(scene.get("count", 0) or 0)
        if count:
            meta_parts.append(f"出现 {count} 次")
        duration = float(scene.get("duration_min", 0.0) or 0.0)
        if duration > 0:
            meta_parts.append(f"约 {int(duration)} 分钟")
        tags = list(scene.get("tags") or [])
        return cls(
            KIND_SCENE,
            title=title,
            text=text,
            time_text=format_ts(scene.get("last_ts") or scene.get("first_ts")),
            meta=" · ".join(meta_parts),
            tags="、".join(str(t) for t in tags[:6]),
            theme=theme,
            parent=parent,
        )

    @classmethod
    def fact_card(cls, fact: dict, theme: str = "light",
                  parent: QWidget | None = None) -> "MemoryCard":
        """陪伴记忆摘要 → 事实卡片。字段：title/text/time_text/meta/tags（兼容扩展）。"""
        return cls(
            KIND_FACT,
            title=str(fact.get("title") or "陪伴事实"),
            text=str(fact.get("text") or ""),
            time_text=format_ts(fact.get("ts") or fact.get("created_ts")),
            meta=str(fact.get("meta") or ""),
            tags=str(fact.get("tags") or ""),
            theme=theme,
            parent=parent,
        )

    @classmethod
    def reflection_card(cls, reflection: dict, theme: str = "light",
                        parent: QWidget | None = None) -> "MemoryCard":
        """反思条目 → 反思卡片。字段：observation/conclusion/confidence/category/status。"""
        conclusion = str(reflection.get("conclusion") or "")
        observation = str(reflection.get("observation") or "")
        text = conclusion or observation or "（无内容）"
        title = str(reflection.get("category") or "反思")
        meta_parts = []
        confidence = reflection.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            meta_parts.append(f"置信度 {confidence:.0%}")
        status = str(reflection.get("status") or "")
        if status:
            meta_parts.append(f"状态:{status}")
        count = int(reflection.get("source_event_count", 0) or 0)
        if count:
            meta_parts.append(f"基于 {count} 条事件")
        return cls(
            KIND_REFLECTION,
            title=title,
            text=text,
            time_text=format_ts(reflection.get("created_ts") or reflection.get("ts")),
            meta=" · ".join(meta_parts),
            tags="",
            theme=theme,
            parent=parent,
        )
