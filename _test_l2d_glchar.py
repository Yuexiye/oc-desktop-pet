"""决定性对比：GLCharWidget + 纯 LAppModel（不经 Live2DRenderer）。

纯测试(QOpenGLWidget+LAppModel)能画，渲染器测试(GLCharWidget+Live2DRenderer)不能。
此测试用 GLCharWidget + 纯 LAppModel，判断问题在 GLCharWidget 还是 Live2DRenderer。

- GLCharWidget+LAppModel 能画 -> 问题在 Live2DRenderer
- GLCharWidget+LAppModel 全黑 -> 问题在 GLCharWidget（format/GL上下文）

单独运行：python _test_l2d_glchar.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

# 关键：先设全局默认 format（与纯测试一致）
from PySide6.QtGui import QSurfaceFormat
fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
fmt.setRenderableType(QSurfaceFormat.OpenGL)
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
QSurfaceFormat.setDefaultFormat(fmt)

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
app = QApplication(sys.argv)

from avatar.gl_char_widget import GLCharWidget

MODEL = r"characters/yuexinmiao/live2d/lafei.model3.json"

class PureL2D(GLCharWidget):
    """GLCharWidget 但用纯 LAppModel 逻辑（不经 Live2DRenderer）"""
    def __init__(self):
        super().__init__()
        self.setFixedSize(300, 400)
        self._model = None
        self._count = 0

    def initializeGL(self):
        self.makeCurrent()
        import live2d.v3 as l2d
        l2d.init()
        l2d.glInit()
        m = l2d.LAppModel()
        m.LoadModelJson(MODEL)
        m.Resize(300, 400)
        cw, ch = m.GetCanvasSizePixel()
        scale = min(300/cw, 400/ch) * 0.9
        m.SetScale(scale)
        print(f"GLCharWidget+纯LAppModel scale={scale:.3f}")
        self._model = m
        self.doneCurrent()

    def paintGL(self):
        if not self._model:
            return
        import live2d.v3 as l2d
        l2d.clearBuffer()
        self._model.Update()
        self._model.Draw()
        self._count += 1
        if self._count == 5:
            self.grabFramebuffer().save(r"logs/l2d_glchar_test.png")
            from PIL import Image
            img = Image.open(r"logs/l2d_glchar_test.png").convert("RGB")
            px = img.load()
            w, h = img.size
            nb = 0
            for y in range(0, h, 2):
                for x in range(0, w, 2):
                    r, g, b = px[x, y]
                    if r > 20 or g > 20 or b > 20:
                        nb += 1
            print(f"GLCharWidget+纯LAppModel: 尺寸{w}x{h} 非黑采样点={nb}")
            app.quit()

win = QWidget()
win.setWindowFlags(Qt.FramelessWindowHint)
win.setAttribute(Qt.WA_TranslucentBackground, True)
lay = QVBoxLayout(win)
lay.setContentsMargins(0, 0, 0, 0)
w = PureL2D()
lay.addWidget(w, 0, Qt.AlignCenter)
win.resize(300, 400)
win.show()
QTimer.singleShot(3000, app.quit)
app.exec()
print("测试结束")