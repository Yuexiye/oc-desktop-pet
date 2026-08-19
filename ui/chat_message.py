"""单条聊天消息渲染 — 角色气泡 / 头像 / 时间戳（P0-6）

参考 N.E.K.O. React ``MessageBubble.tsx`` + ``styles.css`` 设计语言：
- 三类角色：user（右对齐，浅蓝渐变气泡）/ assistant（左对齐，白底气泡）/
  system（居中胶囊 chip）
- 28px 圆形头像（assistant 蓝渐变 / user 浅灰蓝渐变，显示首字）
- meta 行（作者 · 时间，11px 灰字）
- 思考点（ChatThinkingDots 内嵌在 assistant 气泡中）
- 入场动画：淡入 + 高度滑开（textChunkReveal / bubbleFloat 的轻量近似）

纯 QWidget + QSS + QPropertyAnimation，无 QWebEngine。
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui.chat_thinking_dots import ChatThinkingDots

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
VALID_ROLES = (ROLE_USER, ROLE_ASSISTANT, ROLE_SYSTEM)

DEFAULT_AUTHORS = {
    ROLE_USER: "你",
    ROLE_ASSISTANT: "助手",
    ROLE_SYSTEM: "系统",
}

_AVATAR_SIZE = 28          # 圆形头像直径 (px)，对齐 React .avatar 28px
_BUBBLE_MAX_WIDTH = 320    # 气泡最大宽度，对齐 React max-width: min(86%, 320px)


def format_timestamp(ts: float | None) -> str:
    """时间戳 → "HH:MM"（0/None → 空串）。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


class ChatMessage(QFrame):
    """单条消息行：头像 + （meta + 气泡）。system 为居中胶囊（无头像）。

    通过动态属性 ``message-role`` / ``data-theme`` 供 neko.qss 选取样式。

    信号：
        typewriter_clicked() — P2-1：流式打字机期间点击气泡（跳过/显示全文）。
    """

    typewriter_clicked = Signal()

    def __init__(self, role: str = ROLE_ASSISTANT, text: str = "",
                 author: str = "", timestamp: float | None = None,
                 theme: str = "light", thinking: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        if role not in VALID_ROLES:
            role = ROLE_ASSISTANT
        self._role: str = role
        self._theme = theme if theme in ("light", "dark") else "light"
        self._text: str = text
        self._author: str = author or DEFAULT_AUTHORS[role]
        self._thinking: bool = thinking
        self._thinking_dots: ChatThinkingDots | None = None
        self._enter_anim: QPropertyAnimation | None = None
        self._typewriter_active: bool = False  # P2-1：流式打字机进行中（点击跳过）

        self.setObjectName("chatMessage")
        self.setProperty("message-role", role)
        self.setProperty("data-theme", self._theme)

        self._build_ui()
        self.set_timestamp(timestamp if timestamp is not None else time.time())

    # ── 布局 ──

    def _build_ui(self) -> None:
        if self._role == ROLE_SYSTEM:
            self._build_system_ui()
            return
        self._build_bubble_ui()

    def _build_system_ui(self) -> None:
        """系统消息：居中胶囊 chip（时间内嵌）。"""
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 2, 24, 2)
        root.setSpacing(0)
        root.addStretch(1)

        chip = QFrame(self)
        chip.setObjectName("msgBubble")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(10, 6, 10, 6)
        chip_layout.setSpacing(6)

        self._text_label = QLabel(self._text, chip)
        self._text_label.setObjectName("msgText")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # P2-1：流式打字机期间点击可跳过（label 与整行都安装事件过滤器）
        self.installEventFilter(self)
        self._text_label.installEventFilter(self)

        self._meta_label = QLabel(chip)
        self._meta_label.setObjectName("msgMeta")
        self._meta_label.setText(self._meta_text())

        chip_layout.addWidget(self._text_label)
        chip_layout.addWidget(self._meta_label)
        root.addWidget(chip)
        root.addStretch(1)

    def _build_bubble_ui(self) -> None:
        """user/assistant：头像 + （meta + 气泡）。"""
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 2)
        root.setSpacing(8)

        # 头像
        self._avatar = QLabel(self)
        self._avatar.setObjectName("msgAvatar")
        self._avatar.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        self._avatar.setAlignment(Qt.AlignCenter)
        self._avatar.setText(self._avatar_text())
        self._avatar.setCursor(Qt.ArrowCursor)

        # 右侧堆叠：meta + 气泡
        stack = QWidget(self)
        stack.setObjectName("msgStack")
        stack_layout = QVBoxLayout(stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(4)

        self._meta_label = QLabel(stack)
        self._meta_label.setObjectName("msgMeta")
        self._meta_label.setText(self._meta_text())

        bubble = QFrame(stack)
        bubble.setObjectName("msgBubble")
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(9, 9, 9, 9)
        bubble_layout.setSpacing(4)

        self._text_label = QLabel(self._text, bubble)
        self._text_label.setObjectName("msgText")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # P2-1：流式打字机期间点击可跳过（label 与整行都安装事件过滤器）
        self.installEventFilter(self)
        self._text_label.installEventFilter(self)

        self._thinking_dots: ChatThinkingDots | None = None
        bubble_layout.addWidget(self._text_label)
        stack_layout.addWidget(self._meta_label)
        stack_layout.addWidget(bubble)

        stack.setMaximumWidth(_BUBBLE_MAX_WIDTH)
        bubble.setMaximumWidth(_BUBBLE_MAX_WIDTH)

        if self._role == ROLE_USER:
            root.addStretch(1)
            root.addWidget(stack, alignment=Qt.AlignRight)
            root.addWidget(self._avatar)
        else:
            root.addWidget(self._avatar)
            root.addWidget(stack)
            root.addStretch(1)

        self._bubble = bubble
        self._stack = stack
        if self._thinking:
            self.set_thinking(True)

    # ── 文本/元数据 ──

    def _avatar_text(self) -> str:
        return self._author[:1] if self._author else DEFAULT_AUTHORS[self._role][:1]

    def _meta_text(self) -> str:
        ts = format_timestamp(self._timestamp) if hasattr(self, "_timestamp") else ""
        if self._role == ROLE_SYSTEM:
            return ts
        return f"{self._author} · {ts}" if ts else self._author

    def _apply_meta(self) -> None:
        if hasattr(self, "_meta_label") and self._meta_label is not None:
            self._meta_label.setText(self._meta_text())

    # ── 公共 API ──

    @property
    def role(self) -> str:
        return self._role

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        """更新气泡文本（思考态下会先切回文本）。"""
        self._text = text or ""
        if self._thinking:
            self.set_thinking(False)
        if hasattr(self, "_text_label") and self._text_label is not None:
            self._text_label.setText(self._text)

    # ── P2-1 流式打字机（点击跳过）──

    def set_typewriter_active(self, active: bool) -> None:
        """标记流式打字机状态：期间点击气泡立即显示全文（跳过）。

        打字中给文本区加手型光标提示可点击；结束后恢复默认光标。
        """
        self._typewriter_active = bool(active)
        target = self
        if hasattr(self, "_text_label") and self._text_label is not None:
            target = self._text_label
        target.setCursor(Qt.PointingHandCursor if self._typewriter_active else Qt.ArrowCursor)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
        """P2-1：打字机进行中拦截点击 → 发 typewriter_clicked（跳过/显示全文）。"""
        if self._typewriter_active and event.type() == QEvent.MouseButtonPress:
            self.typewriter_clicked.emit()
            return True
        return super().eventFilter(obj, event)

    def set_author(self, author: str) -> None:
        self._author = author or DEFAULT_AUTHORS[self._role]
        if hasattr(self, "_avatar") and self._avatar is not None:
            self._avatar.setText(self._avatar_text())
        self._apply_meta()

    def set_timestamp(self, ts: float | None) -> None:
        self._timestamp = ts if ts is not None else time.time()
        self._apply_meta()

    def set_thinking(self, thinking: bool) -> None:
        """切换思考点：True 显示三点动画（隐藏文本），False 恢复文本。"""
        self._thinking = bool(thinking)
        if self._role == ROLE_SYSTEM:
            return
        if self._thinking:
            if self._thinking_dots is None:
                self._thinking_dots = ChatThinkingDots(self._bubble, theme=self._theme)
                self._thinking_dots.setObjectName("thinkingDots")
                # 插到气泡布局：文本在上，思考点在文本下方（保持气泡高度稳定）
                if self._text_label.parent() is not None:
                    self._bubble.layout().addWidget(self._thinking_dots)
            self._text_label.hide()
            self._thinking_dots.show()
            self._thinking_dots.start()
        else:
            if self._thinking_dots is not None:
                self._thinking_dots.stop()
                self._thinking_dots.hide()
            self._text_label.show()

    def set_theme(self, theme: str) -> None:
        """切换主题（更新动态属性 + 重抛光 + 子组件主题）。"""
        if theme not in ("light", "dark"):
            return
        if theme == self._theme:
            return
        self._theme = theme
        self.setProperty("data-theme", theme)
        if self._thinking_dots is not None:
            self._thinking_dots.set_theme(theme)
        # 重抛光使 QSS 属性选择器生效
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    @property
    def theme(self) -> str:
        return self._theme

    def play_enter_animation(self, duration_ms: int = 200) -> None:
        """入场动画：淡入 + 高度滑开（textChunkReveal 的轻量近似）。"""
        # 高度滑开（对布局友好）
        self.setMaximumHeight(0)
        self.adjustSize()
        target_h = max(self.sizeHint().height(), 1)
        h_anim = QPropertyAnimation(self, b"maximumHeight", self)
        h_anim.setDuration(duration_ms)
        h_anim.setStartValue(0)
        h_anim.setEndValue(target_h)
        h_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 淡入
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(duration_ms)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        self._enter_anim = fade  # 持有引用防 GC
        fade.start()
        h_anim.start()
