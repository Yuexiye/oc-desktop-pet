"""带墨迹装饰的底部字幕条（视觉升级版）。

Amadeus 视频里的字幕条特色：
  - 底部半透明玻璃条（内高光 + 发光边）
  - 上方绿色磷光细边 + 打字机字幕（磷光辉光）
  - 绿色细边下方有黑色「墨迹 / 污渍」溅出装饰（cyberpunk 风，修复原版零高度 bug）

继承 ChatBubble 的打字机逻辑（避免重写 tokenize / layout），只改 paintEvent。
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont
from PySide6.QtWidgets import QWidget

from ui.bubble import ChatBubble
from ui.crt_effects import glass_panel, phosphor_glow_text


# 配色
COLOR_BAR_BG = QColor(8, 12, 16, 215)
COLOR_BAR_BORDER = QColor(120, 255, 160, 110)
COLOR_BAR_TEXT = QColor(225, 235, 225)
COLOR_BAR_TEXT_GLOW = QColor(120, 255, 170)
COLOR_RULE = QColor(120, 255, 160, 150)
COLOR_INK = QColor(0, 0, 0, 235)


class InkSubtitle(ChatBubble):
    """底部字幕条 + 墨迹装饰。

    用法与 ChatBubble 一致：
        sub = InkSubtitle(parent=crt_window)
        sub.set_text("今天确实是你的生日哦。", on_typing_done=cb)
    """

    BAR_HEIGHT = 38  # 底部条带高度
    INK_HEIGHT = 18  # 条带上沿墨迹区高度

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 墨迹缓存：每个实例一次生成（随机种子）
        self._ink_seeds = [
            (random.randint(0, 9999), random.uniform(0.7, 1.6))
            for _ in range(16)
        ]
        # 固定高度
        self.setFixedHeight(self.BAR_HEIGHT + self.INK_HEIGHT)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_y = h - self.BAR_HEIGHT

        # ── 玻璃条 ──
        glass_panel(
            p, QRect(0, bar_y, w, self.BAR_HEIGHT), 0,
            COLOR_BAR_BG, COLOR_BAR_BORDER, COLOR_BAR_BORDER,
            top_highlight=False,
        )

        # ── 顶部绿色磷光细线（cyberpunk 边） ──
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(0.7)
        p.setPen(QPen(COLOR_RULE, 2))
        p.drawLine(0, bar_y, w, bar_y)
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # ── 墨迹（在绿线之上约 INK_HEIGHT 区域溅出，并向下滴进条带） ──
        ink_zone_y = max(0, bar_y - self.INK_HEIGHT)
        ink_zone_h = bar_y + 6 - ink_zone_y  # 略微没入条带
        if ink_zone_h > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(COLOR_INK)
            for seed, scale in self._ink_seeds:
                rng = random.Random(seed)
                x0 = rng.uniform(0, w)
                y0 = ink_zone_y + rng.uniform(0, ink_zone_h * 0.5)
                rx_base = rng.uniform(7, 20) * scale
                ry_base = rng.uniform(5, 12) * scale
                sides = rng.randint(6, 10)
                path = QPainterPath()
                for i in range(sides):
                    ang = (i / sides) * 2 * math.pi + rng.uniform(-0.25, 0.25)
                    rx = rx_base * rng.uniform(0.6, 1.3)
                    ry = ry_base * rng.uniform(0.6, 1.3)
                    px = x0 + rx * math.cos(ang)
                    py = y0 + ry * math.sin(ang)
                    if i == 0:
                        path.moveTo(px, py)
                    else:
                        path.lineTo(px, py)
                path.closeSubpath()
                p.drawPath(path)

        # ── 打字机文字（在条带内居左，磷光辉光） ──
        reveal = (
            self._typewriter_revealed
            if self._is_typing and self._typewriter_revealed > 0
            else len(self._full_text)
        )
        if self._full_text:
            text = self._full_text[:reveal]
            text_rect_y = bar_y + 4
            phosphor_glow_text(
                p, text, 12, text_rect_y,
                QFont("Microsoft YaHei UI", 11),
                COLOR_BAR_TEXT, COLOR_BAR_TEXT_GLOW,
                align=Qt.AlignLeft, glow_passes=2, glow_spread=0.4,
            )
            # 光标
            if self._is_typing and reveal < len(self._full_text):
                fm = p.fontMetrics()
                cursor_x = 12 + fm.horizontalAdvance(text)
                p.setPen(QColor(120, 255, 160, 230))
                p.drawLine(cursor_x, bar_y + 10, cursor_x, bar_y + self.BAR_HEIGHT - 10)

        p.end()
