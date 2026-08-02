"""CRT 内部场景背景层（视觉升级版）。

放在 chrome 内矩形最底层（sprite 之下），提供 Amadeus 实验室场景图。
默认透明，可由 config 配置 PNG/JPG 路径；找不到时显示「会呼吸」的深色实验室氛围
（缓慢漂移的辉光 + 浮尘粒子，营造景深）。

升级点：
  - 氛围辉光缓慢漂移（像远处仪器 / 屏幕的余光）
  - 浮尘粒子缓缓上浮，画面有空气感
  - 顶部 / 底部压暗，让 HUD / 字幕条更清晰
"""
from __future__ import annotations

import math
import os
import random
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QRadialGradient, QBrush
from PySide6.QtWidgets import QWidget


class SceneBackground(QWidget):
    """场景背景层。

    属性:
      image_path: 背景图路径（绝对路径）。
      tint: 背景底色（image_path 缺失或加载失败时用），默认近黑深绿。
      blur: 简单高斯模糊半径（0 = 不模糊；>0 模拟景深）。
      animate: 是否启用氛围漂移 / 浮尘（默认开）。
    """

    def __init__(
        self,
        image_path: Optional[str] = None,
        tint: QColor = QColor(12, 18, 16),
        blur: int = 0,
        animate: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._pixmap: Optional[QPixmap] = None
        self._tint = QColor(tint)
        self._tint2 = QColor(
            max(0, tint.red() - 4), max(0, tint.green() - 4), max(0, tint.blue() - 4)
        )
        self._blur = max(0, int(blur))
        self._animate = animate
        self._phase = 0.0

        # 浮尘粒子（归一化坐标 + 速度 + 半径）
        self._particles = [
            (random.uniform(0, 1), random.uniform(0, 1),
             random.uniform(0.6, 2.2), random.uniform(0.004, 0.016),
             random.uniform(0, math.pi * 2))
            for _ in range(26)
        ]

        self.set_image(image_path)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if animate:
            self._timer.start(40)

    def set_image(self, image_path: Optional[str]) -> None:
        self._pixmap = None
        if image_path and os.path.isfile(image_path):
            pix = QPixmap(image_path)
            if not pix.isNull():
                self._pixmap = pix
        self.update()

    def set_tint(self, color: QColor) -> None:
        self._tint = QColor(color)
        self.update()

    def _tick(self) -> None:
        self._phase += 0.012
        for i, (x, y, r, sp, ph) in enumerate(self._particles):
            y -= sp
            ph += 0.01
            if y < -0.05:
                y = 1.05
                x = random.uniform(0, 1)
            self._particles[i] = (x, y, r, sp, ph)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        # 底色（竖向微渐变，更有层次）
        grad = QRadialGradient(w * 0.5, h * 0.42, max(w, h) * 0.75)
        grad.setColorAt(0.0, self._tint)
        grad.setColorAt(1.0, self._hash_tint_bottom())
        p.fillRect(0, 0, w, h, grad)

        if self._pixmap is not None:
            target = self.rect()
            scaled = self._pixmap.scaled(
                target.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            x = (target.width() - scaled.width()) // 2
            y = (target.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)

        # ── 氛围辉光（缓慢漂移，像远处仪器余光） ──
        if self._animate:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            glows = (
                (0.32 + 0.06 * math.sin(self._phase), 0.36 + 0.04 * math.cos(self._phase * 0.8),
                 QColor(20, 60, 48), 0.22),
                (0.7 + 0.05 * math.cos(self._phase * 0.7), 0.6 + 0.05 * math.sin(self._phase * 0.9),
                 QColor(24, 44, 60), 0.18),
            )
            for gx, gy, gcol, galpha in glows:
                r = max(w, h) * 0.42
                rg = QRadialGradient(w * gx, h * gy, r)
                rg.setColorAt(0.0, QColor(gcol.red(), gcol.green(), gcol.blue(), int(90 * galpha * 4)))
                rg.setColorAt(1.0, QColor(gcol.red(), gcol.green(), gcol.blue(), 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(rg))
                p.drawRect(0, 0, w, h)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # ── 浮尘粒子 ──
            for (fx, fy, fr, _, fph) in self._particles:
                px = fx * w + math.sin(fph) * 3
                py = fy * h
                a = int(40 + 30 * (0.5 + 0.5 * math.sin(fph)))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(180, 220, 200, a))
                p.drawEllipse(int(px), int(py), int(fr), int(fr))

        # ── 顶 / 底压暗（突出 HUD / 字幕条） ──
        p.fillRect(0, 0, w, 26, QColor(0, 0, 0, 95))
        p.fillRect(0, h - 58, w, 58, QColor(0, 0, 0, 120))

        p.end()

    def _hash_tint_bottom(self) -> QColor:
        t = self._tint
        return QColor(max(0, t.red() - 6), max(0, t.green() - 6), max(0, t.blue() - 6))
