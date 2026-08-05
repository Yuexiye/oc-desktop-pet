"""用正式桌宠的 Live2DRenderer + GLCharWidget 做最小显示测试。

如果这个能画出拉菲 -> 问题在 pet.py 的集成（其他东西干扰）
如果这个也画不出   -> 问题在 Live2DRenderer 内部

单独运行：python _test_l2d_renderer.py
会保存 logs/l2d_renderer_test.png 并打印非黑采样点。
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtCore import Qt, QTimer

fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
fmt.setRenderableType(QSurfaceFormat.OpenGL)
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
QSurfaceFormat.setDefaultFormat(fmt)

app = QApplication(sys.argv)

# 用正式桌宠的渲染器
from avatar.live2d_renderer import Live2DRenderer
from avatar.gl_char_widget import GLCharWidget

win = QWidget()
win.setWindowFlags(Qt.FramelessWindowHint)
win.setAttribute(Qt.WA_TranslucentBackground, True)
lay = QVBoxLayout(win)
lay.setContentsMargins(0, 0, 0, 0)

# 模拟 pet.py：create_renderer 返回渲染器，char_label = renderer.label
renderer = Live2DRenderer(win)
char_label = renderer.label
lay.addWidget(char_label, 0, Qt.AlignCenter)

win.resize(300, 400)
win.show()

# 手动触发 GL 初始化（GLCharWidget.initializeGL 会自动调 on_gl_initialized）
# 等几帧后截图
count = [0]
def tick():
    count[0] += 1
    if count[0] == 10:
        try:
            char_label.grabFramebuffer().save(r"logs/l2d_renderer_test.png")
            from PIL import Image
            img = Image.open(r"logs/l2d_renderer_test.png").convert("RGB")
            px = img.load()
            w, h = img.size
            nb = 0
            for y in range(0, h, 2):
                for x in range(0, w, 2):
                    r, g, b = px[x, y]
                    if r > 20 or g > 20 or b > 20:
                        nb += 1
            print(f"渲染器测试: 尺寸{w}x{h} 非黑采样点={nb}")
        except Exception as e:
            print("截图失败:", e)
        app.quit()

t = QTimer()
t.timeout.connect(tick)
t.start(100)
QTimer.singleShot(3000, app.quit)
app.exec()
print("测试结束")