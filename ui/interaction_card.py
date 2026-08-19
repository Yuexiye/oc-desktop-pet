"""互动卡片 — 游戏邀请 / 音乐推荐 / 休息建议（P2-3/P2-4/P2-5）

neko_palette 风格的可点卡片：
- 可嵌入 ChatPanel 消息列表（add_card），也可作为桌宠旁的悬浮气泡卡
  （pet.py 持有一个实例，显示/隐藏）。
- 动作按钮（如「开始游戏 / 播放 / 好，休息一下」）点击发
  ``action_clicked(kind, action_id)``，由接线层（pet.py）统一处理。
- ``data-theme`` 动态属性 + 内联 QSS（light/dark 双主题，对齐 neko.qss）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme.neko_palette import palette

logger = logging.getLogger(__name__)

_KIND_ACCENTS = {
    "game": ("#2f68df", "#6aa1ff"),
    "music": ("#7d5fc4", "#9d82e0"),
    "rest": ("#236845", "#4caf7d"),
}


class InteractionCard(QFrame):
    """一张可点互动卡片（标题 / 正文 / 动作按钮 / 关闭）。"""

    action_clicked = Signal(str, str)      # kind, action_id
    dismiss_requested = Signal(str)        # kind

    def __init__(self, kind: str = "", title: str = "", body: str = "",
                 actions: list[tuple[str, str]] | None = None,
                 theme: str = "light", parent: QWidget | None = None,
                 show_dismiss: bool = True):
        super().__init__(parent)
        self._kind = str(kind or "")
        self._theme = theme if theme in ("light", "dark") else "light"
        self._show_dismiss = bool(show_dismiss)

        self.setObjectName("interactionCard")
        self.setProperty("card-kind", self._kind)
        self.setProperty("data-theme", self._theme)

        self._build_ui()
        self._apply_qss()
        self.set_content(kind=self._kind, title=title, body=body, actions=actions)

    # ── UI 构建 ──

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        # 头行：kind 标签 + 关闭
        head = QHBoxLayout()
        head.setSpacing(6)
        self._kind_label = QLabel(self)
        self._kind_label.setObjectName("icKind")
        self._kind_label.setText(self._kind_text())
        head.addWidget(self._kind_label)
        head.addStretch(1)
        if self._show_dismiss:
            self._dismiss_button = QPushButton("✕", self)
            self._dismiss_button.setObjectName("icDismiss")
            self._dismiss_button.setFixedWidth(24)
            self._dismiss_button.setCursor(Qt.PointingHandCursor)
            self._dismiss_button.clicked.connect(
                lambda: self.dismiss_requested.emit(self._kind)
            )
            head.addWidget(self._dismiss_button)
        root.addLayout(head)

        self._title_label = QLabel(self)
        self._title_label.setObjectName("icTitle")
        self._title_label.setWordWrap(True)
        root.addWidget(self._title_label)

        self._body_label = QLabel(self)
        self._body_label.setObjectName("icBody")
        self._body_label.setWordWrap(True)
        self._body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._body_label)

        # 动作按钮行（可换行）
        self._actions_box = QHBoxLayout()
        self._actions_box.setSpacing(8)
        self._action_buttons: list[QPushButton] = []
        self._actions_map: dict[str, str] = {}
        root.addLayout(self._actions_box)

        self._dismiss_button_ref = getattr(self, "_dismiss_button", None)

    def _kind_text(self) -> str:
        mapping = {"game": "🎮 小游戏", "music": "🎵 音乐", "rest": "💧 休息"}
        return mapping.get(self._kind, self._kind or "互动")

    def _apply_qss(self) -> None:
        """内联 QSS（light/dark 双主题，对齐 neko_palette）。"""
        try:
            p = palette("light")
            pd = palette("dark")
            accent, accent_dark = _KIND_ACCENTS.get(self._kind, ("#2f68df", "#6aa1ff"))
            self.setStyleSheet(
                f"""
                InteractionCard {{
                    background: {p["card_bg"]};
                    border: 1px solid rgba(53,72,104,0.08);
                    border-radius: 14px;
                }}
                InteractionCard[data-theme="dark"] {{
                    background: {pd["card_bg"]};
                    border: 1px solid rgba(148,166,196,0.10);
                }}
                InteractionCard QLabel#icKind {{
                    color: {accent};
                    font-size: 10px;
                    font-weight: 700;
                    letter-spacing: 0.06em;
                    background: transparent;
                }}
                InteractionCard[data-theme="dark"] QLabel#icKind {{
                    color: {accent_dark};
                }}
                InteractionCard QLabel#icTitle {{
                    color: {p["card_text"]};
                    font-size: 13px;
                    font-weight: 600;
                    background: transparent;
                }}
                InteractionCard[data-theme="dark"] QLabel#icTitle {{
                    color: {pd["card_text"]};
                }}
                InteractionCard QLabel#icBody {{
                    color: {p["card_meta"]};
                    font-size: 12px;
                    background: transparent;
                }}
                InteractionCard[data-theme="dark"] QLabel#icBody {{
                    color: {pd["card_meta"]};
                }}
                InteractionCard QPushButton#icAction {{
                    border: 1px solid rgba(47,104,223,0.35);
                    border-radius: 999px;
                    background: rgba(47,104,223,0.08);
                    color: #2f68df;
                    padding: 4px 12px;
                    font-size: 11px;
                }}
                InteractionCard QPushButton#icAction:hover {{
                    background: rgba(47,104,223,0.16);
                    border-color: rgba(47,104,223,0.6);
                }}
                InteractionCard[data-theme="dark"] QPushButton#icAction {{
                    border-color: rgba(106,161,255,0.4);
                    background: rgba(106,161,255,0.12);
                    color: #6aa1ff;
                }}
                InteractionCard[data-theme="dark"] QPushButton#icAction:hover {{
                    background: rgba(106,161,255,0.22);
                }}
                InteractionCard QPushButton#icDismiss {{
                    border: none;
                    border-radius: 12px;
                    background: rgba(131,148,175,0.12);
                    color: {p["card_meta"]};
                    font-size: 12px;
                }}
                InteractionCard QPushButton#icDismiss:hover {{
                    background: rgba(131,148,175,0.24);
                }}
                InteractionCard[data-theme="dark"] QPushButton#icDismiss {{
                    color: {pd["card_meta"]};
                    background: rgba(120,140,170,0.14);
                }}
                """
            )
        except Exception as exc:
            logger.debug("InteractionCard QSS 应用失败: %s", exc)

    # ── 内容 API ──

    def set_content(self, kind: str = "", title: str = "", body: str = "",
                    actions: list[tuple[str, str]] | None = None) -> None:
        """更新卡片内容（kind 变化时重建样式）。"""
        kind = str(kind or "")
        if kind and kind != self._kind:
            self._kind = kind
            self.setProperty("card-kind", kind)
            self._kind_label.setText(self._kind_text())
            self._apply_qss()
            style = self.style()
            style.unpolish(self)
            style.polish(self)
        self._title_label.setText(title or "")
        self._body_label.setText(body or "")
        self._rebuild_actions(actions or [])

    def _rebuild_actions(self, actions: list[tuple[str, str]]) -> None:
        for btn in self._action_buttons:
            self._actions_box.removeWidget(btn)
            btn.deleteLater()
        self._action_buttons = []
        self._actions_map = {}
        for label, action_id in actions:
            btn = QPushButton(str(label), self)
            btn.setObjectName("icAction")
            btn.setCursor(Qt.PointingHandCursor)
            aid = str(action_id)
            self._actions_map[aid] = aid
            btn.clicked.connect(
                lambda checked=False, a=aid: self.action_clicked.emit(self._kind, a)
            )
            self._actions_box.addWidget(btn)
            self._action_buttons.append(btn)
        self._actions_box.addStretch(1)

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def action_ids(self) -> list[str]:
        return list(self._actions_map.keys())

    @property
    def title_text(self) -> str:
        return self._title_label.text()

    @property
    def body_text(self) -> str:
        return self._body_label.text()

    # ── 主题 ──

    def set_theme(self, theme: str) -> None:
        if theme not in ("light", "dark") or theme == self._theme:
            return
        self._theme = theme
        self.setProperty("data-theme", theme)
        self._apply_qss()
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    @property
    def theme(self) -> str:
        return self._theme


__all__ = ["InteractionCard"]
