"""记忆展示面板 — 事件 / 场景 / 事实 / 反思 四类卡片（P0-8 + P1-2/P1-3）

UI重构: 继承 PanelWidget 基类，统一标题栏、刷新按钮、关闭按钮

数据来源：``~/.oc-pet/memory/`` 真实文件（与 core 层同一目录约定）：
- 事件：``<agent_id>_events.jsonl``（每行一个 JSON 事件）
- 场景：``<agent_id>_scenes.json``（``{"scenes": [...]}``）
- 事实：``<agent_id>_facts.json``（FactStore，P1-2）；文件缺失/为空时回退
  ``<agent_id>.json``（CompanionMemory 摘要）
- 反思：``<agent_id>_reflections.json``（ReflectionEngine，P1-3）

空数据/文件缺失 → 占位文案（"还没有 XX 记忆…"）。所有文件读取带
防御式异常处理，损坏文件跳过该条，绝不崩溃。

纯 QWidget + QSS；读取为同步小文件（KB 级），只应在主线程调用。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from ui.memory_card import (
    KIND_EVENT, KIND_SCENE, KIND_FACT, KIND_REFLECTION, MemoryCard,
)
from ui.panel_widget import PanelWidget

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".oc-pet" / "memory"

EVENT_PREVIEW_LIMIT = 30   # 事件卡片上限（最新 N 条）
SCENE_PREVIEW_LIMIT = 20   # 场景卡片上限
FACT_PREVIEW_LIMIT = 10    # 事实卡片上限（FactStore + 陪伴摘要回退）
REFLECTION_PREVIEW_LIMIT = 10  # 反思卡片上限


# ── 数据读取（纯函数，便于单测）──────────────────────────────────

def read_events(agent_id: str, memory_dir: str | Path | None = None,
                limit: int = EVENT_PREVIEW_LIMIT) -> list[dict]:
    """读取事件流 jsonl，返回最新 N 条（每条 dict；损坏行跳过）。"""
    d = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    path = d / f"{agent_id}_events.jsonl"
    events: list[dict] = []
    try:
        if not path.exists():
            return events
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                logger.debug("记忆面板跳过坏事件行: %.60s", line)
                continue
    except OSError as exc:
        logger.warning("记忆面板读事件失败: %s", exc)
        return []
    events.sort(key=lambda e: float(e.get("ts", 0.0) or 0.0), reverse=True)
    return events[:limit]


def read_scenes(agent_id: str, memory_dir: str | Path | None = None,
                limit: int = SCENE_PREVIEW_LIMIT) -> list[dict]:
    """读取场景表，返回最新 N 条（按 last_ts 倒序）。"""
    d = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    path = d / f"{agent_id}_scenes.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        scenes = list(data.get("scenes", []) or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("记忆面板读场景失败: %s", exc)
        return []
    scenes.sort(key=lambda s: float(s.get("last_ts", 0.0) or s.get("first_ts", 0.0) or 0.0),
                reverse=True)
    return scenes[:limit]


def read_fact_store(agent_id: str, memory_dir: str | Path | None = None,
                    limit: int = FACT_PREVIEW_LIMIT) -> list[dict]:
    """从 FactStore 文件（``<agent_id>_facts.json``）读取事实卡片数据（P1-2）。

    每条事实渲染为一张卡片：标题=分类、正文=事实文本、meta=置信度/状态。
    """
    d = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    path = d / f"{agent_id}_facts.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        facts = list(data.get("facts", []) or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("记忆面板读事实库失败: %s", exc)
        return []

    cards: list[dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        text = str(f.get("text") or "").strip()
        if not text:
            continue
        meta_parts = []
        confidence = f.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            meta_parts.append(f"置信度 {confidence:.0%}")
        status = str(f.get("status") or "")
        if status and status != "pending":
            meta_parts.append(f"状态:{status}")
        evidence_count = len(f.get("evidence") or [])
        if evidence_count:
            meta_parts.append(f"证据 {evidence_count} 条")
        cards.append({
            "title": str(f.get("category") or "事实"),
            "text": text,
            "ts": f.get("created_ts"),
            "meta": " · ".join(meta_parts),
            "tags": "",
        })
    cards.sort(key=lambda c: float(c.get("ts", 0.0) or 0.0), reverse=True)
    return cards[:limit]


def read_facts(agent_id: str, memory_dir: str | Path | None = None,
               limit: int = FACT_PREVIEW_LIMIT) -> list[dict]:
    """读取事实卡片数据（P1-2 起优先 FactStore 文件）。

    - ``<agent_id>_facts.json``（FactStore，LLM 抽取的真实事实）存在且有内容 → 用之；
    - 否则回退 ``<agent_id>.json``（CompanionMemory 摘要：累计陪伴天数 / 连续活跃 /
      最近话题 / 今日分类计数），保持旧行为。
    """
    store_cards = read_fact_store(agent_id, memory_dir, limit)
    if store_cards:
        return store_cards
    return _read_companion_facts(agent_id, memory_dir, limit)


def _read_companion_facts(agent_id: str, memory_dir: str | Path | None,
                          limit: int) -> list[dict]:
    """从 CompanionMemory 摘要提取"事实"卡片数据（旧行为回退路径）。

    事实 = 陪伴摘要里可读的确定信息：累计陪伴天数 / 连续活跃天数 /
    最近话题 / 今日分类计数。每项渲染为一张事实卡片。
    """
    d = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    path = d / f"{agent_id}.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("记忆面板读陪伴摘要失败: %s", exc)
        return []

    facts: list[dict] = []
    total_days = int(data.get("total_days", 0) or 0)
    if total_days > 0:
        facts.append({"title": "累计陪伴", "text": f"已经陪伴了 {total_days} 天"})
    streak = int(data.get("streak_days", 0) or 0)
    if streak > 0:
        facts.append({"title": "连续活跃", "text": f"连续活跃 {streak} 天"})
    last_topic = str(data.get("last_topic", "") or "")
    if last_topic:
        facts.append({"title": "最近话题", "text": last_topic})
    today = data.get("today", {}) or {}
    if isinstance(today, dict) and today:
        items = [f"{k}:{v}" for k, v in today.items() if v]
        if items:
            facts.append({"title": "今日活动", "text": "，".join(items)})
    last_active = str(data.get("last_active_date", "") or "")
    if last_active:
        facts.append({"title": "上次见面", "text": last_active})
    return facts[:limit]


def read_reflections(agent_id: str, memory_dir: str | Path | None = None,
                     limit: int = REFLECTION_PREVIEW_LIMIT) -> list[dict]:
    """从反思文件（``<agent_id>_reflections.json``）读取反思卡片数据（P1-3）。

    每条 insight 渲染为一张卡片：标题=分类、正文=结论（缺省观察）、
    meta=置信度/状态/基于事件数。
    """
    d = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    path = d / f"{agent_id}_reflections.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        reflections = list(data.get("reflections", []) or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("记忆面板读反思失败: %s", exc)
        return []

    cards: list[dict] = []
    for r in reflections:
        if not isinstance(r, dict):
            continue
        conclusion = str(r.get("conclusion") or "")
        observation = str(r.get("observation") or "")
        text = conclusion or observation
        if not text:
            continue
        meta_parts = []
        confidence = r.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            meta_parts.append(f"置信度 {confidence:.0%}")
        status = str(r.get("status") or "")
        if status and status != "pending":
            meta_parts.append(f"状态:{status}")
        count = int(r.get("source_event_count", 0) or 0)
        if count:
            meta_parts.append(f"基于 {count} 条事件")
        cards.append({
            "title": str(r.get("category") or "反思"),
            "text": text,
            "ts": r.get("created_ts") or r.get("ts"),
            "meta": " · ".join(meta_parts),
            "tags": "",
        })
    cards.sort(key=lambda c: float(c.get("ts", 0.0) or 0.0), reverse=True)
    return cards[:limit]


def read_memory_data(agent_id: str, memory_dir: str | Path | None = None) -> dict:
    """一次性读取四类记忆数据。返回 {"events": [...], "scenes": [...], "facts": [...], "reflections": [...]}。"""
    return {
        "events": read_events(agent_id, memory_dir),
        "scenes": read_scenes(agent_id, memory_dir),
        "facts": read_facts(agent_id, memory_dir),
        "reflections": read_reflections(agent_id, memory_dir),
    }


# ── 面板 ──

class MemoryPanel(PanelWidget):
    """记忆展示面板：三个分区（事件/场景/事实）+ 占位文案 + 刷新按钮。
    
    继承 PanelWidget，统一标题栏、刷新按钮、关闭按钮。
    """

    def __init__(self, agent_id: str = "default", memory_dir: str | Path | None = None,
                 theme: str = "light", parent: QWidget | None = None):
        super().__init__("记忆", parent, show_refresh=True, show_close=True,
                        min_size=(400, 520), max_size=(600, 800))
        self._agent_id = agent_id or "default"
        self._memory_dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self._theme = theme
        self._cards: list[MemoryCard] = []
        
        # 填充内容区域
        self._build_content()
        
        # 刷新按钮连接
        self.refresh_requested.connect(self.reload)
        
        # 加载数据
        self.reload()

    # ── UI 构建 ──

    def _build_content(self) -> None:
        """构建内容区域（滚动列表）"""
        self._scroll = QScrollArea()
        self._scroll.setObjectName("memoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget(self._scroll)
        container.setObjectName("memoryListContainer")
        self._list_layout = QVBoxLayout(container)
        self._list_layout.setContentsMargins(12, 12, 12, 12)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(container)
        self.content_layout.addWidget(self._scroll, stretch=1)

    def _title_text(self) -> str:
        return f"记忆 · {self._agent_id}"

    # ── 数据加载 ──

    def set_agent(self, agent_id: str) -> None:
        """切换数据源 agent 并刷新。"""
        self._agent_id = agent_id or "default"
        self.set_title(self._title_text())
        self.reload()

    def reload(self) -> None:
        """重新读取真实记忆文件并重建卡片（主线程调用）。"""
        data = read_memory_data(self._agent_id, self._memory_dir)
        self._rebuild(data)

    def _rebuild(self, data: dict) -> None:
        # 清空旧卡片
        for card in self._cards:
            self._list_layout.removeWidget(card)
            card.deleteLater()
        self._cards = []

        events = data.get("events", []) or []
        scenes = data.get("scenes", []) or []
        facts = data.get("facts", []) or []
        reflections = data.get("reflections", []) or []

        self._add_section(KIND_EVENT, events, MemoryCard.event_card)
        self._add_section(KIND_SCENE, scenes, MemoryCard.scene_card)
        self._add_section(KIND_FACT, facts, MemoryCard.fact_card)
        self._add_section(KIND_REFLECTION, reflections, MemoryCard.reflection_card)

        # 全空 → 总占位
        if not events and not scenes and not facts and not reflections:
            self._add_empty("还没有记忆，先去和 ta 聊聊天、做点事吧～")

    def _add_section(self, kind: str, items: list[dict],
                     factory) -> None:
        """渲染一个分区：标题 + 卡片（或空占位）。"""
        from ui.memory_card import KIND_LABELS
        header = QLabel(f"{KIND_LABELS[kind]} · {len(items)}", self)
        header.setObjectName("memorySectionHeader")
        self._list_layout.insertWidget(self._list_layout.count() - 1, header)
        if not items:
            self._add_empty(f"还没有{KIND_LABELS[kind]}记忆…")
            return
        for item in items:
            card = factory(item, theme=self._theme, parent=self)
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

    def _add_empty(self, text: str) -> None:
        label = QLabel(text, self)
        label.setObjectName("memoryEmpty")
        label.setAlignment(Qt.AlignCenter)
        self._list_layout.insertWidget(self._list_layout.count() - 1, label)

    # ── 主题 ──

    def set_theme(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            return
        if theme == self._theme:
            return
        self._theme = theme
        self.setProperty("data-theme", theme)
        for card in self._cards:
            card.set_theme(theme)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def card_count(self) -> int:
        return len(self._cards)

    @property
    def cards(self) -> list[MemoryCard]:
        return list(self._cards)
