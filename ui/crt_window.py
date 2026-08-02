"""复古 CRT 显示器外观窗口（视觉升级版）。

把任意 QWidget content 装进 CRT chrome 中：拉丝金属边框 + 四角螺丝 + 散热孔
+ 内圈玻璃 + AMADEUS 印刷字（磷光辉光）+ 绿色电源 LED（磷光呼吸）
+ 调节旋钮。视频里 Amadeus 桌宠就是这种视觉风格。

升级点：
  - 拉丝金属 + 螺丝 + 散热孔，更像真实硬件
  - 印刷字 / LED 用磷光辉光（crt_effects）
  - 更强的球面暗角（屏幕弧度）
  - 开机扫描动画 play_power_on()：亮线展开 → 白屏闪现 → 内容浮现

层叠顺序（从下到上）：
  chrome → scene_background → content_widget → crt_overlay
         → [hud] → [subtitle] → [power_on_overlay]
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRect, QPoint, QTimer, QSize
from PySide6.QtGui import (
    QPainter, QColor, QPainterPath, QFont,
    QLinearGradient, QRadialGradient, QPen, QBrush, QPixmap,
)
from PySide6.QtWidgets import QWidget

from ui.crt_effects import phosphor_glow_ellipse, phosphor_glow_text


# ── 几何常量（CRT chrome） ────────────────────────────
CHROME_THICKNESS = 40       # 金属边框厚度（略加厚，给螺丝/散热孔留空间）
BEZEL_INNER_PAD = 10        # 玻璃到 chrome 内缘距离
AMADEUS_LOGO_W = 84         # 左下 AMADEUS 印刷字宽
AMADEUS_LOGO_H = 13
LED_RADIUS = 4              # 电源 LED 半径
LED_OFFSET_RIGHT = 22       # LED 距右缘
LED_OFFSET_BOTTOM = 18      # LED 距底缘
KNOB_RADIUS = 5             # 调节旋钮半径
KNOB_OFFSETS = ((-28, -40), (-48, -40))  # 右下两个旋钮 (dx, dy)

# ── 配色（CRT 金属 / 玻璃） ────────────────────────────
COLOR_CHROME_TOP = QColor(64, 62, 58)
COLOR_CHROME_MID = QColor(40, 38, 35)
COLOR_CHROME_BOTTOM = QColor(20, 20, 18)
COLOR_BEZEL_INNER = QColor(6, 6, 6)
COLOR_LOGO_TEXT = QColor(200, 194, 176)
COLOR_LOGO_GLOW = QColor(120, 255, 170)
COLOR_LED_ON = QColor(130, 255, 170)
COLOR_LED_GLOW = QColor(120, 255, 160)
COLOR_KNOB_LIGHT = QColor(120, 118, 110)
COLOR_KNOB_DARK = QColor(46, 44, 40)
COLOR_SCREW = QColor(150, 148, 140)
COLOR_SCREW_DARK = QColor(30, 30, 28)


class _PowerOnOverlay(QWidget):
    """开机扫描动画：亮线展开 → 白屏闪现 → 内容浮现。盖在最上层。"""

    def __init__(self, parent: "CRTWindow"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._progress = 1.0  # 默认已完成（不挡内容）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def play(self, duration_ms: int = 750):
        self._duration = max(120, duration_ms)
        self._progress = 0.0
        self.show()
        self.raise_()
        self._timer.start(16)

    def _tick(self):
        self._progress = min(1.0, self._progress + 16 / self._duration)
        self.update()
        if self._progress >= 1.0:
            self._timer.stop()
            self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        prog = self._progress

        # 黑场覆盖（前期不透明，后期淡出）
        cover = int(255 * max(0.0, 1.0 - prog * 1.25))
        if cover > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, cover))
            p.drawRect(0, 0, w, h)

        # 亮线展开阶段
        if prog < 0.62:
            # 0→0.28：细亮线出现（带辉光）
            # 0.28→0.62：纵向展开铺满
            open_t = max(0.0, min(1.0, prog / 0.62))
            line_h = max(2, int(h * open_t))
            ly = h // 2 - line_h // 2
            # 辉光
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.setOpacity(0.8)
            glow = QRect(0, ly - 6, w, line_h + 12)
            p.fillRect(glow, QColor(220, 255, 235, 90))
            p.setOpacity(1.0)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # 亮线本体
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(235, 255, 245))
            p.drawRect(0, ly, w, line_h)

        # 白屏闪现（在亮线铺满后短暂过曝，再淡出）
        if prog >= 0.55:
            flash = int(200 * max(0.0, 1.0 - (prog - 0.55) / 0.45))
            if flash > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, flash))
                p.drawRect(0, 0, w, h)

        p.end()


class CRTWindow(QWidget):
    """CRT 外观窗口。

    自己是顶级窗口（frame-less, on-top, tool），paintEvent 画 chrome。
    set_content / set_scene_background / set_overlay / set_hud / set_subtitle
    接受子控件并自动布局到 chrome 内矩形。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._content: Optional[QWidget] = None
        self._overlay: Optional[QWidget] = None
        self._hud: Optional[QWidget] = None
        self._subtitle: Optional[QWidget] = None
        self._scene_bg: Optional[QWidget] = None

        # LED 呼吸动画相位
        self._led_phase = 0.0
        self._led_timer = QTimer(self)
        self._led_timer.timeout.connect(self._tick_led)
        self._led_timer.start(120)  # ~8fps 呼吸

        # chrome 缓存（拉丝金属 + 螺丝 + 散热孔，只在 resize 时重绘）
        self._chrome_cache: Optional[QPixmap] = None
        self._chrome_size = QSize(0, 0)

        # 开机动画 overlay（最上层）
        self._power_on = _PowerOnOverlay(self)
        self._power_on.setGeometry(self.rect())

    # ── 子控件注册 ──

    def set_content(self, widget: QWidget) -> None:
        self._content = widget
        widget.setParent(self)
        widget.show()
        self._layout_children()

    def set_scene_background(self, widget: QWidget) -> None:
        self._scene_bg = widget
        widget.setParent(self)
        widget.lower()
        widget.show()
        self._layout_children()

    def set_overlay(self, widget: QWidget) -> None:
        self._overlay = widget
        widget.setParent(self)
        widget.raise_()
        widget.show()
        self._layout_children()

    def set_hud(self, widget: QWidget) -> None:
        self._hud = widget
        widget.setParent(self)
        widget.raise_()
        widget.show()
        self._layout_children()

    def set_subtitle(self, widget: QWidget) -> None:
        self._subtitle = widget
        widget.setParent(self)
        widget.raise_()
        widget.show()
        self._layout_children()

    def play_power_on(self, duration_ms: int = 750):
        """播放 CRT 开机扫描动画。"""
        self._power_on.play(duration_ms)

    # ── 几何 ──

    def content_rect(self) -> QRect:
        """chrome 内的内容矩形（场景 + sprite + overlay 都在这个区域）。"""
        c = CHROME_THICKNESS + BEZEL_INNER_PAD
        return QRect(c, c, self.width() - 2 * c, self.height() - 2 * c)

    def _layout_children(self) -> None:
        rect = self.content_rect()
        for w in (self._scene_bg, self._content, self._overlay):
            if w is not None:
                w.setGeometry(rect)
        if self._hud is not None:
            hx = rect.right() - self._hud.width() - 12
            hy = rect.top() + 12
            self._hud.move(hx, hy)
        if self._subtitle is not None:
            sx = rect.left()
            sy = rect.bottom() - self._subtitle.height()
            self._subtitle.setGeometry(sx, sy, rect.width(), self._subtitle.height())
        # HUD / subtitle / power-on 在 overlay 之上
        if self._overlay is not None:
            if self._hud is not None:
                self._hud.raise_()
            if self._subtitle is not None:
                self._subtitle.raise_()
        self._power_on.setGeometry(self.rect())
        self._power_on.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._chrome_cache = None  # 触发重建
        self._layout_children()

    # ── LED 呼吸 ──

    def _tick_led(self) -> None:
        self._led_phase = (self._led_phase + 0.08) % (2 * math.pi)
        self.update()

    # ── chrome 缓存构建 ──

    def _build_chrome(self) -> None:
        w, h = self.width(), self.height()
        px = QPixmap(w, h)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)

        # 金属边框（垂直渐变）
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, COLOR_CHROME_TOP)
        grad.setColorAt(0.5, COLOR_CHROME_MID)
        grad.setColorAt(1.0, COLOR_CHROME_BOTTOM)
        p.fillRect(0, 0, w, h, grad)

        # 拉丝纹理（细水平发丝线）
        p.setPen(QColor(255, 255, 255, 7))
        y = 1
        while y < h:
            p.drawLine(0, y, w, y)
            y += 2

        # 内圈 bezel（深色玻璃）
        bezel_rect = self.content_rect().adjusted(
            -BEZEL_INNER_PAD, -BEZEL_INNER_PAD, BEZEL_INNER_PAD, BEZEL_INNER_PAD
        )
        p.fillRect(bezel_rect, COLOR_BEZEL_INNER)

        # 顶部高光（金属反光）
        highlight = QLinearGradient(0, 0, 0, 8)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 80))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, w, 8, highlight)

        # 底部散热孔（一排短横槽）
        slot_color = QColor(0, 0, 0, 120)
        p.setPen(slot_color)
        p.setBrush(slot_color)
        slot_w, slot_h, gap = 16, 3, 7
        total = 6 * slot_w + 5 * gap
        sx0 = (w - total) // 2
        sy0 = h - CHROME_THICKNESS // 2 - 1
        for i in range(6):
            rx = sx0 + i * (slot_w + gap)
            p.fillRect(rx, sy0, slot_w, slot_h, slot_color)

        # 四角螺丝
        for cx, cy in (
            (14, 14), (w - 14, 14), (14, h - 14), (w - 14, h - 14)
        ):
            p.setPen(Qt.NoPen)
            p.setBrush(COLOR_SCREW_DARK)
            p.drawEllipse(QPoint(cx, cy), 6, 6)
            p.setBrush(COLOR_SCREW)
            p.drawEllipse(QPoint(cx - 1, cy - 1), 4, 4)
            # 一字槽
            p.setPen(QPen(COLOR_SCREW_DARK, 1))
            p.drawLine(cx - 3, cy, cx + 3, cy)

        # 左下角 AMADEUS 印刷字（实色，辉光在 paintEvent 动态画）
        logo_x = 10
        logo_y = h - CHROME_THICKNESS + (CHROME_THICKNESS - AMADEUS_LOGO_H) // 2
        p.setPen(COLOR_LOGO_TEXT)
        p.setFont(QFont("Courier New", 9, QFont.Bold))
        p.drawText(
            logo_x, logo_y, AMADEUS_LOGO_W, AMADEUS_LOGO_H,
            Qt.AlignLeft | Qt.AlignVCenter, "AMADEUS",
        )

        # 右下角 调节旋钮（两个）
        for dx, dy in KNOB_OFFSETS:
            kx = w + dx
            ky = h + dy
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawEllipse(QPoint(kx + 1, ky + 1), KNOB_RADIUS, KNOB_RADIUS)
            knob_grad = QRadialGradient(kx, ky - 1, KNOB_RADIUS)
            knob_grad.setColorAt(0.0, COLOR_KNOB_LIGHT)
            knob_grad.setColorAt(1.0, COLOR_KNOB_DARK)
            p.setBrush(QBrush(knob_grad))
            p.drawEllipse(QPoint(kx, ky), KNOB_RADIUS, KNOB_RADIUS)
            p.setPen(QPen(QColor(225, 220, 205), 1))
            p.drawLine(kx, ky - KNOB_RADIUS + 1, kx, ky - 1)

        p.end()
        self._chrome_cache = px
        self._chrome_size = QSize(w, h)

    # ── 绘制 ──

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if self._chrome_cache is None or self._chrome_size != QSize(w, h):
            self._build_chrome()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._chrome_cache)

        # 左下 AMADEUS 印刷字：磷光辉光（叠在 chrome 之上）
        logo_x = 10
        logo_y = h - CHROME_THICKNESS + (CHROME_THICKNESS - AMADEUS_LOGO_H) // 2
        phosphor_glow_text(
            p, "AMADEUS", logo_x, logo_y,
            QFont("Courier New", 9, QFont.Bold),
            COLOR_LOGO_TEXT, COLOR_LOGO_GLOW,
            align=Qt.AlignLeft, glow_passes=3, glow_spread=0.5,
        )

        # 右下 电源 LED（磷光呼吸辉光）
        led_x = w - LED_OFFSET_RIGHT
        led_y = h - LED_OFFSET_BOTTOM
        breath = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(self._led_phase))
        # 本体亮度随呼吸
        led_color = QColor(
            int(COLOR_LED_ON.red() * breath + 30),
            int(COLOR_LED_ON.green() * breath + 30),
            int(COLOR_LED_ON.blue() * breath + 30),
        )
        phosphor_glow_ellipse(
            p, led_x, led_y, LED_RADIUS, led_color, COLOR_LED_GLOW,
            passes=4, spread=2.6,
        )
        # LED 高光点
        p.setBrush(QColor(225, 255, 235, 230))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(led_x - 1, led_y - 1), max(1, LED_RADIUS // 2), max(1, LED_RADIUS // 2))

        p.end()
