"""专注呼吸辉光 overlay — 聊天面板边缘内发光（P0-7）

参考 N.E.K.O. React ``styles.css`` 的 ``chat-focus-overlay`` /
``@keyframes focus-glow-breathe-full``（3.4s ease-in-out 呼吸）。

实现：透明 QWidget 叠在聊天面板上（``WA_TransparentForMouseEvents``），
``QPropertyAnimation`` 驱动 ``glow_intensity``（0→1→0，3400ms，无限循环），
``paintEvent`` 用两层圆角描边绘制"内嵌亮带 + 外圈柔晕"。

视觉强度 = ``strength``（config ``focus.glow_strength`` 0~1）×
``glow_intensity``（呼吸相位）。``strength<=0`` 或未激活时零视觉：
不显示、不动画（P0-7 验收「glow_strength=0 时零视觉」）。
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ui.theme.neko_palette import glow_rgb

BREATHE_PERIOD_MS = 3400      # 呼吸周期，对齐 CSS 3.4s
GLOW_CORNER_RADIUS = 16       # 与聊天面板圆角一致

_ALPHA_HALO = 40              # 外层柔晕 alpha（峰值 × strength）
_ALPHA_BAND = 72              # 内层亮带 alpha（峰值 × strength）


class FocusOverlay(QWidget):
    """专注辉光层：``set_active(True, strength)`` 开始呼吸，``False`` 立即消失。

    父窗口须在 ``resizeEvent`` 里 ``overlay.setGeometry(self.rect())`` 对齐。
    """

    def __init__(self, parent=None, theme: str = "light"):
        super().__init__(parent)
        self._theme = theme if theme in ("light", "dark") else "light"
        self._strength: float = 0.0     # config focus.glow_strength（0~1）
        self._intensity: float = 0.0    # 动画驱动相位（0~1）
        self._active: bool = False

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName("focusOverlay")
        self.hide()

        # 呼吸动画：0 → 1 → 0，InOutSine 缓动，无限循环
        self._anim = QPropertyAnimation(self, b"glow_intensity", self)
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.0)
        self._anim.setDuration(BREATHE_PERIOD_MS)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)

    # ── 动画属性（QPropertyAnimation 目标）──

    def _get_intensity(self) -> float:
        return self._intensity

    def _set_intensity(self, value: object) -> None:
        self._intensity = max(0.0, min(1.0, float(value)))
        self.update()

    glow_intensity = Property(float, _get_intensity, _set_intensity)

    # ── 公共 API ──

    def set_active(self, active: bool, strength: float | None = None) -> None:
        """激活/关闭辉光。

        Args:
            active: 专注是否开启。
            strength: config ``focus.glow_strength``（0~1）；``None`` 保持现值。
                0 或负值 → 强制零视觉（隐藏 + 停动画）。
        """
        if strength is not None:
            self._strength = max(0.0, min(1.0, float(strength)))
        want = bool(active) and self._strength > 0.0
        if want == self._active:
            if strength is not None:
                self.update()  # 强度变化也要重绘
            return
        self._active = want
        if want:
            self._anim.start()
            self.show()
            self.raise_()
        else:
            self._anim.stop()
            self._intensity = 0.0
            self.hide()
            self.update()

    def is_active(self) -> bool:
        return self._active

    def set_strength(self, strength: float) -> None:
        """单独更新强度（不改变激活状态）。"""
        self._strength = max(0.0, min(1.0, float(strength)))
        if self._strength <= 0.0 and self._active:
            self.set_active(False, strength=self._strength)
        else:
            self.update()

    def set_theme(self, theme: str) -> None:
        if theme in ("light", "dark"):
            self._theme = theme
            self.update()

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def strength(self) -> float:
        return self._strength

    def animation(self) -> QPropertyAnimation:
        """返回呼吸动画（验收用：检查 state() == Running）。"""
        return self._anim

    # ── 绘制 ──

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if not self._active or self._strength <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 呼吸相位：峰值强度随 glow_intensity 在 [0.35, 1.0] 间摆动
        k = self._strength * (0.35 + 0.65 * self._intensity)
        glow = glow_rgb(self._theme)
        deep = glow_rgb(self._theme, deep=True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, GLOW_CORNER_RADIUS, GLOW_CORNER_RADIUS)

        # 外圈柔晕（宽描边、低 alpha）
        halo = QPen(QColor(*glow, int(_ALPHA_HALO * k)), 10.0,
                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(halo)
        p.drawPath(path)

        # 内层亮带（窄描边、高 alpha）
        band = QPen(QColor(*deep, int(_ALPHA_BAND * k)), 3.0,
                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(band)
        p.drawPath(path)

        p.end()
