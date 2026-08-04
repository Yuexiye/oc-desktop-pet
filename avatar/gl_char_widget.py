"""GLCharWidget - 承载 Live2D 的透明 OpenGL 表面

pet.py 把角色当成一个 QLabel（char_label）来用：setPixmap / move / installEventFilter /
mapToGlobal / setFixedSize 等。Live2D 不能走 QLabel 的 pixmap 路线，所以这里用
QOpenGLWidget 作为真正的渲染表面，同时提供一组 QLabel 兼容的「空方法」，让 pet.py 现有
调用（如 _rescale_current_frame 里的 setPixmap）不报错。

绘制委托给 Live2DRenderer：本控件在 paintGL 中调用 renderer.draw()，由 live2d-py 在
当前 GL 上下文里更新并绘制模型。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QSurfaceFormat


class GLCharWidget(QOpenGLWidget):
    """透明 OpenGL 角色表面（Live2D 用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # live2d Cubism 需要 OpenGL 2.0+（glad 加载 GL 函数），且用固定管线
        # （glMatrixMode/glOrtho）绘制。必须用 Compatibility Profile——
        # Core Profile 移除了固定管线，导致 glMatrixMode 报 GL_INVALID_OPERATION
        # 且 live2d 模型画不出来。
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        self.setFormat(fmt)

        self._renderer = None  # Live2DRenderer 反向引用（绘制时回调）
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    # ── 与 Live2DRenderer 对接 ──

    def set_renderer(self, renderer) -> None:
        self._renderer = renderer

    def initializeGL(self) -> None:
        if self._renderer is not None:
            # 确保当前 GL 上下文激活后再初始化 live2d（glad 需要 current context）
            self.makeCurrent()
            self._renderer.on_gl_initialized()
            self.doneCurrent()

    def paintGL(self) -> None:
        if self._renderer is not None:
            self._renderer.draw()

    def resizeGL(self, w: int, h: int) -> None:
        if self._renderer is not None:
            self._renderer.on_resize(w, h)

    # ── QLabel 兼容空方法（pet.py 会调用，对 GL 表面无意义） ──

    def setPixmap(self, *a, **k):  # noqa: D401
        """Live2D 不走 pixmap，忽略。"""
        return None

    def setText(self, *a, **k):
        return None

    def setStyleSheet(self, *a, **k):
        return None

    def setAlignment(self, *a, **k):
        return None

    def setWordWrap(self, *a, **k):
        return None

    def setObjectName(self, name: str):  # noqa: D401
        # 允许 pet.py / 设计系统按 objectName 套用样式（对 GL 表面无视觉作用）
        super().setObjectName(name)
