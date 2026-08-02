"""状态面板 HUD — 玻璃拟态需求条，悬浮在宠物头顶。

显示 5 项养成属性（饱腹/口渴/心情/精力/健康），颜色随数值高低变化
（高=青绿、中=琥珀、低=红），一眼看清宠物需求。主题感知（跟随 ThemeManager）。

设计为桌宠窗口子控件，透明、不拦截鼠标。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ui.emotion_face import EMOTION_LABEL


# 行定义：(emoji, 名称, 取值键)
_ROWS = (
    ("🍖", "饱腹", "hunger"),
    ("💧", "口渴", "thirst"),
    ("😊", "心情", "mood"),
    ("⚡", "精力", "energy"),
    ("❤️", "健康", "health"),
)


def _bar_color(ratio: float, dark: bool) -> QColor:
    """数值高低→颜色（绿→琥珀→红）"""
    if ratio >= 0.6:
        return QColor(80, 200, 170) if dark else QColor(40, 170, 130)
    if ratio >= 0.3:
        return QColor(240, 190, 90) if dark else QColor(220, 160, 50)
    return QColor(235, 95, 95) if dark else QColor(210, 70, 70)


class StatusHUD(QWidget):
    """头顶需求面板（玻璃拟态）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(Qt.FramelessWindowHint)

        self._theme = "dark"
        self._stats: dict[str, tuple[float, float]] = {}
        self._row_h = 22
        self._pad = 12
        self._bar_h = 7
        self._emo_row_h = 20
        self._emotion = "neutral"
        self.setFixedSize(
            188,
            self._pad * 2 + 18 + len(_ROWS) * self._row_h + self._emo_row_h,
        )

        try:
            from ui.theme import get_default
            mgr = get_default()
            if mgr is not None:
                self._theme = mgr.current
                mgr.theme_changed.connect(self._on_theme)
        except Exception:
            pass

    def _on_theme(self, theme: str):
        self._theme = theme
        self.update()

    def set_stats(self, save) -> None:
        """从 PetSave 实例刷新数值"""
        try:
            self._stats = {
                "hunger": (float(save.hunger), 100.0),
                "thirst": (float(save.thirst), 100.0),
                "mood": (float(save.mood), float(save.mood_max)),
                "energy": (float(save.energy), 100.0),
                "health": (float(save.health), float(save.health_max)),
            }
            self.update()
        except Exception:
            pass

    def set_emotion(self, emotion: str) -> None:
        """设置当前情绪（与头顶情绪脸同步），底部显示文案"""
        if emotion not in EMOTION_LABEL:
            emotion = "neutral"
        if emotion == self._emotion:
            return
        self._emotion = emotion
        self.update()

    def paintEvent(self, event):
        if not self._stats:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = self._theme == "dark"

        w, h = self.width(), self.height()
        r = 16
        # 玻璃面板
        bg = QColor(20, 24, 46, 226) if dark else QColor(255, 248, 238, 232)
        border = QColor(120, 140, 200, 110) if dark else QColor(180, 150, 120, 130)
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, r, r)
        p.fillPath(path, bg)
        p.setPen(border)
        p.drawPath(path)

        # 标题
        p.setPen(QColor(220, 226, 240) if dark else QColor(70, 50, 35))
        p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        p.drawText(self._pad, self._pad + 2, "状态")

        # 各属性条
        text_col = QColor(214, 220, 236) if dark else QColor(80, 55, 40)
        track_col = QColor(255, 255, 255, 38) if dark else QColor(0, 0, 0, 30)
        y = self._pad + 18
        for emoji, name, key in _ROWS:
            val, mx = self._stats.get(key, (0.0, 100.0))
            ratio = max(0.0, min(1.0, val / mx)) if mx > 0 else 0.0

            p.setPen(text_col)
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self._pad, y + self._row_h - 7, f"{emoji} {name}")

            # 条 track
            bar_x = self._pad + 52
            bar_w = w - bar_x - self._pad - 34
            track_rect = (bar_x, y + self._row_h // 2 - self._bar_h // 2, bar_w, self._bar_h)
            p.setPen(Qt.NoPen)
            p.setBrush(track_col)
            p.drawRoundedRect(*track_rect, 3, 3)

            # 条 fill
            fill_w = max(2, int(bar_w * ratio))
            p.setBrush(_bar_color(ratio, dark))
            p.drawRoundedRect(bar_x, track_rect[1], fill_w, self._bar_h, 3, 3)

            # 数值
            p.setPen(text_col)
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.drawText(w - self._pad - 30, y + self._row_h - 7, f"{int(val)}")

            y += self._row_h

        # 当前情绪（底部文案，与头顶情绪脸同步）
        label = EMOTION_LABEL.get(self._emotion, "平静")
        p.setPen(text_col)
        p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
        p.drawText(self._pad, self.height() - self._emo_row_h + 14, "当前情绪")
        # 情绪色点
        emo_dot = {
            "happy": QColor(120, 210, 150),
            "sad": QColor(130, 170, 235),
            "thinking": QColor(200, 180, 140),
            "surprised": QColor(240, 200, 120),
            "angry": QColor(235, 120, 120),
            "neutral": QColor(180, 190, 205),
        }.get(self._emotion, QColor(180, 190, 205))
        p.setBrush(emo_dot)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self._pad + 64, self.height() - self._emo_row_h + 6, 9, 9)
        p.setPen(text_col)
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(self._pad + 78, self.height() - self._emo_row_h + 14, label)

        p.end()
