"""验证透明背景对 Live2D 绘制的影响。

正式桌宠窗口是 WA_TranslucentBackground（透明无边框），QOpenGLWidget 也设了透明。
如果透明背景导致 live2d 画不出，这个测试会复现；如果不影响，这个测试能画出。

单独运行：python _test_l2d_transparent.py
会保存 logs/l2d_transparent.png，并打印非黑采样点。
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtCore import Qt, QTimer

# Compatibility Profile（live2d 需要）
fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
fmt.setRenderableType(QSurfaceFormat.OpenGL)
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
QSurfaceFormat.setDefaultFormat(fmt)

app = QApplication(sys.argv)

MODEL = r"characters/yuexinmiao/live2d/lafei.model3.json"

class Live2DWidget(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(300, 400)
        # 与正式桌宠一致：透明背景
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._model = None
        self._count = 0

    def initializeGL(self):
        import live2d.v3 as l2d
        l2d.init()
        l2d.glInit()
        m = l2d.LAppModel()
        m.LoadModelJson(MODEL)
        m.Resize(300, 400)
        cw, ch = m.GetCanvasSizePixel()
        scale = min(300/cw, 400/ch) * 0.9
        m.SetScale(scale)
        print(f"透明测试 scale={scale:.3f}")
        self._model = m

    def paintGL(self):
        if not self._model:
            return
        import live2d.v3 as l2d
        l2d.clearBuffer()
        self._model.Update()
        self._model.Draw()
        self._count += 1
        if self._count == 5:
            self.grabFramebuffer().save(r"logs/l2d_transparent.png")
            from PIL import Image
            img = Image.open(r"logs/l2d_transparent.png").convert("RGBA")
            px = img.load()
            w, h = img.size
            nonblack = 0
            for y in range(0, h, 2):
                for x in range(0, w, 2):
                    r, g, b, a = px[x, y]
                    if r > 20 or g > 20 or b > 20:
                        nonblack += 1
            print(f"透明背景: 尺寸{w}x{h} RGBA非黑采样点={nonblack}")
            app.quit()

# 外层容器：透明无边框，模拟正式桌宠
win = QWidget()
win.setWindowFlags(Qt.FramelessWindowHint)
win.setAttribute(Qt.WA_TranslucentBackground, True)
lay = QVBoxLayout(win)
lay.setContentsMargins(0, 0, 0, 0)
w = Live2DWidget()
lay.addWidget(w)
win.resize(300, 400)
win.show()
QTimer.singleShot(3000, app.quit)
app.exec()
print("测试结束")