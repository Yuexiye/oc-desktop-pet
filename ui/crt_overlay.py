"""CRT 屏幕后处理：扫描线 / 噪点 / 暗角 / RGB 磷光栅格 / 玻璃眩光。

浮在 sprite 之上但低于 HUD / 字幕条，半透明 overlay。可独立启用 / 关闭 / 调强度。
新增（视觉升级）：
  - rgb_mask：纵向 RGB 子像素栅格，经典 CRT 显像质感
  - scanline drift：扫描线缓慢爬行，屏幕「活」起来
  - glare：左上方柔和玻璃高光，模拟球面玻璃反光
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QImage, QRadialGradient
from PySide6.QtWidgets import QWidget


class CRTOverlay(QWidget):
    """CRT 后处理 overlay。

    效果开关：
      scanlines : 横向扫描线（带缓慢漂移）
      noise     : 随机雪花噪点（os.urandom 一次成型，无逐像素循环）
      vignette  : 径向暗角
      rgb_mask  : 纵向 RGB 磷光栅格
      glare     : 左上玻璃高光
    """

    def __init__(
        self,
        scanlines: bool = True,
        noise: bool = True,
        vignette: bool = True,
        rgb_mask: bool = True,
        glare: bool = True,
        scanline_alpha: int = 70,
        noise_alpha: int = 30,
        rgb_alpha: int = 26,
        glare_alpha: int = 40,
        noise_cell: int = 2,
        noise_interval_ms: int = 80,
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._scanlines_enabled = scanlines
        self._noise_enabled = noise
        self._vignette_enabled = vignette
        self._rgb_enabled = rgb_mask
        self._glare_enabled = glare

        self._scanline_alpha = max(0, min(255, scanline_alpha))
        self._noise_alpha = max(0, min(255, noise_alpha))
        self._rgb_alpha = max(0, min(255, rgb_alpha))
        self._glare_alpha = max(0, min(255, glare_alpha))
        self._noise_cell = max(1, int(noise_cell))

        self._noise_w = 0
        self._noise_h = 0
        self._noise_img: QImage | None = None

        self._scan_offset = 0

        # 噪点刷新定时器
        self._noise_timer = QTimer(self)
        self._noise_timer.timeout.connect(self._regen_noise)
        if noise:
            self._noise_timer.start(noise_interval_ms)

        # 扫描线漂移定时器
        self._drift_timer = QTimer(self)
        self._drift_timer.timeout.connect(self._tick_drift)
        if scanlines:
            self._drift_timer.start(45)

    # ── 开关 ──
    def set_scanlines(self, enabled: bool) -> None:
        self._scanlines_enabled = enabled
        self.update()

    def set_noise(self, enabled: bool) -> None:
        self._noise_enabled = enabled
        if enabled and not self._noise_timer.isActive():
            self._noise_timer.start()
        elif not enabled and self._noise_timer.isActive():
            self._noise_timer.stop()
        self.update()

    def set_vignette(self, enabled: bool) -> None:
        self._vignette_enabled = enabled
        self.update()

    def set_rgb_mask(self, enabled: bool) -> None:
        self._rgb_enabled = enabled
        self.update()

    def set_glare(self, enabled: bool) -> None:
        self._glare_enabled = enabled
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._regen_noise()

    def _regen_noise(self) -> None:
        if not self._noise_enabled:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        cell = self._noise_cell
        nw = (w + cell - 1) // cell
        nh = (h + cell - 1) // cell
        # os.urandom 一次成型：无 Python 逐像素循环，开销极低，每帧换新 → 真实雪花闪烁
        raw = os.urandom(nw * nh)
        self._noise_img = QImage(raw, nw, nh, QImage.Format_Grayscale8)
        self._noise_w = nw
        self._noise_h = nh
        self.update()

    def _tick_drift(self) -> None:
        self._scan_offset = (self._scan_offset + 1) % 4
        if self._scanlines_enabled:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()

        # ── 扫描线（每隔 2px 一条，带缓慢漂移） ──
        if self._scanlines_enabled and self._scanline_alpha > 0:
            p.setPen(QColor(0, 0, 0, self._scanline_alpha))
            y = self._scan_offset
            while y < h:
                p.drawLine(0, y, w, y)
                y += 2

        # ── 噪点（低 alpha grayscale 拉伸到全屏） ──
        if self._noise_enabled and self._noise_img is not None and self._noise_alpha > 0:
            p.setOpacity(self._noise_alpha / 255.0)
            scaled = self._noise_img.scaled(
                w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation
            )
            p.drawImage(0, 0, scaled)
            p.setOpacity(1.0)

        # ── RGB 磷光栅格（把 3px 宽 R/G/B 图像拉伸到全屏 → 纵向彩色子像素） ──
        if self._rgb_enabled and self._rgb_alpha > 0:
            mask = QImage(3, 1, QImage.Format_RGB32)
            mask.setPixelColor(0, 0, QColor(255, 40, 40))
            mask.setPixelColor(1, 0, QColor(40, 255, 40))
            mask.setPixelColor(2, 0, QColor(40, 40, 255))
            p.setOpacity(self._rgb_alpha / 255.0)
            p.drawImage(QRect(0, 0, w, h), mask)
            p.setOpacity(1.0)

        # ── 暗角（径向渐变：中心透明，四周黑色） ──
        if self._vignette_enabled:
            rg = QRadialGradient(w / 2, h / 2, max(w, h) * 0.55)
            rg.setColorAt(0.0, QColor(0, 0, 0, 0))
            rg.setColorAt(0.7, QColor(0, 0, 0, 60))
            rg.setColorAt(1.0, QColor(0, 0, 0, 185))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(rg))
            p.drawRect(0, 0, w, h)

        # ── 玻璃眩光（左上方柔和高光，模拟球面反光） ──
        if self._glare_enabled and self._glare_alpha > 0:
            gx, gy = w * 0.32, h * 0.18
            gr = max(w, h) * 0.6
            gg = QRadialGradient(gx, gy, gr)
            gg.setColorAt(0.0, QColor(255, 255, 255, self._glare_alpha))
            gg.setColorAt(0.4, QColor(255, 255, 255, self._glare_alpha // 3))
            gg.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(gg))
            p.drawRect(0, 0, w, h)

        p.end()
