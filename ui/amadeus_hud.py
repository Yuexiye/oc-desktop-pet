"""Amadeus 风格 HUD —— 绿字 monospace + 圆点状态灯（视觉升级版）。

升级点：
  - 玻璃质感面板（半透明深底 + 顶部内高光 + 发光描边）
  - 文字磷光辉光
  - 状态点带呼吸脉冲，整体「活」起来
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QWidget

from ui.crt_effects import glass_panel, phosphor_glow_text, phosphor_glow_ellipse


# 配色（cyberpunk 绿 + 暗底）
COLOR_BG = QColor(8, 16, 13, 170)
COLOR_BORDER = QColor(120, 255, 160, 110)
COLOR_BORDER_GLOW = QColor(120, 255, 160, 120)
COLOR_TEXT_DIM = QColor(150, 195, 172, 220)
COLOR_TEXT_BRIGHT = QColor(150, 255, 190)
COLOR_DOT_RUNNING = QColor(120, 255, 160)
COLOR_DOT_NEEDYOU = QColor(255, 220, 100)
COLOR_DOT_ACTIVE = QColor(120, 200, 255)
COLOR_DOT_IDLE = QColor(140, 140, 140)


class AmadeusHUD(QWidget):
    """右上角 Amadeus 风格 HUD。

    调用 set_counts(running, need_you, active) 更新数字。
    数字 0 = 灰色，>0 = 主题色（绿 / 黄 / 蓝）。状态点带呼吸脉冲。
    """

    DOT_RUNNING = 0
    DOT_NEEDYOU = 1
    DOT_ACTIVE = 2

    DOT_COLORS = (COLOR_DOT_RUNNING, COLOR_DOT_NEEDYOU, COLOR_DOT_ACTIVE)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._running = 0
        self._need_you = 0
        self._active = 0
        self._pulse = 0.0

        self._pad_x = 12
        self._pad_y = 7
        self._dot_r = 4
        self._gap = 14

        self._font = QFont("Consolas", 9)
        self._font.setStyleHint(QFont.Monospace)
        self.setFont(self._font)

        self._update_size()

        # 状态点呼吸脉冲
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start(500)

    def set_counts(self, running: int, need_you: int, active: int) -> None:
        self._running = max(0, int(running))
        self._need_you = max(0, int(need_you))
        self._active = max(0, int(active))
        self.update()

    def _tick_pulse(self) -> None:
        self._pulse = (self._pulse + 0.4) % (2 * math.pi)
        self.update()

    def _update_size(self) -> None:
        fm = QFontMetrics(self._font)
        sample = "0 RUNNING · 0 NEED YOU · 0 ACTIVE"
        text_w = fm.horizontalAdvance(sample)
        text_h = fm.height()
        w = text_w + self._pad_x * 2 + self._dot_r * 2 * 3 + self._gap * 3
        h = text_h + self._pad_y * 2
        self.setFixedSize(w, h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 玻璃面板
        glass_panel(
            p, QRect(0, 0, w, h), 9,
            COLOR_BG, COLOR_BORDER, COLOR_BORDER_GLOW, top_highlight=True,
        )

        # 三组 [圆点 + 数字 + 文字]
        p.setFont(self._font)
        fm = p.fontMetrics()
        text_y = (h - fm.height()) // 2

        sections = (
            (self._running, "RUNNING", self.DOT_RUNNING),
            (self._need_you, "NEED YOU", self.DOT_NEEDYOU),
            (self._active, "ACTIVE", self.DOT_ACTIVE),
        )

        x = self._pad_x
        pulse_a = 0.75 + 0.25 * (0.5 + 0.5 * math.sin(self._pulse))
        for idx, (count, label, dot_kind) in enumerate(sections):
            active = count > 0
            dot_color = self.DOT_COLORS[dot_kind] if active else COLOR_DOT_IDLE
            cy = h // 2

            if active:
                # 呼吸脉冲辉光
                glow = QColor(dot_color)
                glow.setAlpha(int(160 * pulse_a))
                phosphor_glow_ellipse(p, x + self._dot_r, cy, self._dot_r,
                                      dot_color, glow, passes=3, spread=2.0)
            else:
                p.setBrush(dot_color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPoint(x + self._dot_r, cy), self._dot_r, self._dot_r)

            x += self._dot_r * 2 + 4

            seg = f"{count} {label}"
            text_color = COLOR_TEXT_BRIGHT if active else COLOR_TEXT_DIM
            # 数字用更亮的绿做辉光，标签用本色
            phosphor_glow_text(
                p, seg, x, text_y,
                self._font, text_color, COLOR_TEXT_BRIGHT,
                align=Qt.AlignLeft, glow_passes=2, glow_spread=0.45,
            )
            x += fm.horizontalAdvance(seg)

            if idx < len(sections) - 1:
                sep = " · "
                p.setPen(COLOR_TEXT_DIM)
                p.drawText(x, text_y, sep)
                x += fm.horizontalAdvance(sep)

        p.end()
