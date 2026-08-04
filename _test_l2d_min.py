"""最小 Live2D 渲染测试：验证 live2d-py 能否在 PySide6 QOpenGLWidget 画出拉菲。

单独运行：python _test_l2d_min.py
如果能画出，会保存 logs/l2d_min_test.png（非全黑）。
这是验证 live2d-py 方案是否可行的关键测试。
"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtCore import QTimer

# 用 Compatibility Profile（live2d 用 legacy 固定管线）
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
        self._l2d = None
        self._model = None
        self._draw_count = 0

    def initializeGL(self):
        import live2d.v3 as l2d
        self._l2d = l2d
        l2d.init()
        l2d.glInit()
        print("glInit OK")
        m = l2d.LAppModel()
        m.LoadModelJson(MODEL)
        print("LoadModelJson OK")
        m.Resize(300, 400)
        print("Resize OK")
        # 用像素画布缩放
        cw, ch = m.GetCanvasSizePixel()
        scale = min(300/cw, 400/ch) * 0.9
        m.SetScale(scale)
        print(f"canvas_px={cw}x{ch} scale={scale:.3f}")
        self._model = m

    def paintGL(self):
        if not self._model:
            return
        import live2d.v3 as l2d
        l2d.clearBuffer()
        self._model.Update(0.016)
        self._model.Draw()
        self._draw_count += 1
        # 第 5 帧后截图保存
        if self._draw_count == 5:
            self.grabFramebuffer().save(r"logs/l2d_min_test.png")
            print("已保存 logs/l2d_min_test.png")
            # 检查非黑像素
            from PIL import Image
            img = Image.open(r"logs/l2d_min_test.png").convert("RGB")
            px = img.load()
            w, h = img.size
            nonblack = 0
            for y in range(0, h, 4):
                for x in range(0, w, 4):
                    r, g, b = px[x, y]
                    if r > 20 or g > 20 or b > 20:
                        nonblack += 1
            print(f"非黑采样点: {nonblack} (w={w} h={h})")
            app.quit()

w = Live2DWidget()
w.show()
QTimer.singleShot(3000, app.quit)
app.exec()
print("测试结束")