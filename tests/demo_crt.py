"""CRT 效果独立 demo —— 不启动整个 oc-pet 也能看视觉效果（视觉升级版）。

跑这个：
    python tests/demo_crt.py

会在屏幕弹出一个 CRT 显示器窗口，里面有：
  - 拉丝金属边框 + 四角螺丝 + 散热孔 + AMADEUS 磷光印刷字 + 绿色呼吸 LED + 调节旋钮
  - 会呼吸的实验室场景（漂移辉光 + 浮尘粒子）
  - 活着的占位 mascot（呼吸 + 眨眼 + 轻浮 + 磷光晕）
  - 扫描线（缓慢漂移）+ 雪花噪点 + RGB 磷光栅格 + 球面暗角 + 玻璃眩光
  - 右上角玻璃 HUD（绿字磷光 + 状态点脉冲）
  - 底部玻璃字幕条 + 墨迹装饰 + 打字机（循环测试文本）
  - 开机时播放 CRT 扫描亮线动画

如果视觉效果 OK，下一步就可以挂到 pet.py 里（替换或包裹现有 PetWindow）。
"""
import sys
import math
import random
import logging
from pathlib import Path

# 让 oc-pet 的 ui/ 包可导入
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ── 静音：这个独立 demo 不应该往启动它的终端刷任何信息 ──────────
# 1) 关掉 Qt 的 C++ 层日志（Windows 平台的 qpa / fonts / GL 信息在真实
#    显示环境下会疯狂往 cmd 里刷，正是你看到「一直在滚动一大堆信息」的元凶）。
import os

os.environ.setdefault(
    "QT_LOGGING_RULES",
    "*.debug=false;*.info=false;*.warning=false;*.critical=false;*.fatal=false;"
    "qt.qpa.*=false;qt.qpa.fonts.*=false;qt.qpa.windows.*=false",
)

# 2) 拦截 Python logging：任何被间接导入的模块如果在导入期调用 basicConfig
#    开 StreamHandler，都会往控制台打。这里直接把 basicConfig 变空操作并挂 NullHandler。
logging.basicConfig = lambda *a, **k: None  # type: ignore[assignment]
for _h in list(logging.root.handlers):
    logging.root.removeHandler(_h)
logging.root.addHandler(logging.NullHandler())
logging.root.setLevel(logging.CRITICAL + 1)

from PySide6.QtCore import Qt, QTimer, qInstallMessageHandler


def _silent_qt_message(msg_type, context, message):  # noqa: ANN001
    """丢弃 Qt 的所有 C++ 层日志，不让它出现在启动 demo 的终端里。"""
    return


qInstallMessageHandler(_silent_qt_message)

from PySide6.QtGui import QPainter, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ui.crt_window import CRTWindow
from ui.scene_background import SceneBackground
from ui.crt_overlay import CRTOverlay
from ui.ink_subtitle import InkSubtitle
from ui.amadeus_hud import AmadeusHUD
from ui.crt_effects import phosphor_glow_text, phosphor_glow_ellipse


class PlaceholderSprite(QWidget):
    """活着的占位 mascot：呼吸 + 眨眼 + 轻浮 + 磷光晕。

    真集成时这里换成 sprite_renderer.LazySpriteRenderer 或 Live2DRenderer。
    """

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._t = 0.0
        self._blink = 0.0

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(50)

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._maybe_blink)
        self._blink_timer.start(110)

    def _tick(self):
        self._t += 0.08
        self.update()

    def _maybe_blink(self):
        if random.random() < 0.04:
            self._blink = 1.0
        elif self._blink > 0:
            self._blink = max(0.0, self._blink - 0.3)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx = w / 2
        cy = h * 0.46

        breathe = 1.0 + 0.035 * math.sin(self._t)
        bob = math.sin(self._t * 0.6) * 4
        cy += bob

        # 磷光晕（让角色像在发光）
        aura = QColor(120, 255, 170, 40)
        phosphor_glow_ellipse(p, cx, cy, 70 * breathe, QColor(20, 40, 30, 0), aura,
                              passes=3, spread=2.2)

        # 身体（随呼吸轻微纵向缩放）
        body_w, body_h = 180 * breathe, 160 * breathe
        p.setBrush(QColor(245, 240, 230))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(cx - body_w / 2), int(cy - body_h / 2),
                     int(body_w), int(body_h))

        # 耳朵
        ear_color = QColor(245, 240, 230)
        p.setBrush(ear_color)
        for s in (-1, 1):
            bx = cx + s * 55
            ear = QPainterPath()
            ear.moveTo(bx - 26, cy - body_h / 2 + 18)
            ear.lineTo(bx, cy - body_h / 2 - 60)
            ear.lineTo(bx + 26, cy - body_h / 2 + 18)
            ear.closeSubpath()
            p.drawPath(ear)

        # 眼睛（眨眼：blink>0.5 时画闭眼线）
        eye_y = cy + 10
        ex_l, ex_r = cx - 38, cx + 22
        p.setBrush(QColor(40, 60, 90))
        p.setPen(Qt.NoPen)
        if self._blink > 0.5:
            p.setPen(QPen(QColor(40, 60, 90), 3))
            p.drawLine(ex_l - 10, eye_y, ex_l + 10, eye_y)
            p.drawLine(ex_r - 10, eye_y, ex_r + 10, eye_y)
            p.setPen(Qt.NoPen)
        else:
            p.drawEllipse(int(ex_l - 9), int(eye_y - 12), 18, 24)
            p.drawEllipse(int(ex_r - 9), int(eye_y - 12), 18, 24)
            p.setBrush(QColor(220, 235, 255, 220))
            p.drawEllipse(int(ex_l - 4), int(eye_y - 8), 5, 6)
            p.drawEllipse(int(ex_r - 4), int(eye_y - 8), 5, 6)

        # 鼻子 + 嘴
        p.setBrush(QColor(220, 140, 140))
        p.drawEllipse(int(cx - 6), int(cy + 44), 12, 10)
        p.setPen(QColor(180, 120, 120))
        p.drawLine(int(cx), int(cy + 54), int(cx - 12), int(cy + 64))
        p.drawLine(int(cx), int(cy + 54), int(cx + 12), int(cy + 64))

        # 名字标签（磷光）
        phosphor_glow_text(
            p, "月薪喵 · yuexinmiao", int(cx - 70), int(cy + 90),
            QFont("Microsoft YaHei UI", 10),
            QColor(220, 230, 210), QColor(120, 255, 170),
            align=Qt.AlignCenter, glow_passes=2, glow_spread=0.45,
        )

        p.end()


def main():
    app = QApplication(sys.argv)

    # ── CRT chrome 外壳 ──
    crt = CRTWindow()
    crt.resize(540, 420)

    # ── 场景背景（会呼吸的实验室氛围） ──
    bg = SceneBackground(image_path=None, tint=QColor(14, 22, 18))

    # ── 活着的占位 mascot ──
    sprite = PlaceholderSprite()

    # ── CRT 后处理（扫描线漂移 + 雪花 + RGB 栅格 + 暗角 + 眩光） ──
    overlay = CRTOverlay(
        scanlines=True,
        noise=True,
        vignette=True,
        rgb_mask=True,
        glare=True,
        scanline_alpha=60,
        noise_alpha=28,
        rgb_alpha=22,
        glare_alpha=38,
    )

    # ── HUD：右上角绿字状态灯（玻璃 + 磷光 + 脉冲） ──
    hud = AmadeusHUD()
    hud.set_counts(1, 0, 1)

    # ── 字幕条：循环显示测试文本（模拟流式 TTS） ──
    subtitle = InkSubtitle()
    test_lines = [
        "啊，这样啊。今天确实是你的生日哦。",
        "Photoshop 和 Visual Studio Code，正在帮我跑代码呢。",
        "El Psy Kongroo. — Amadeus",
    ]
    state = {"idx": 0}

    def next_line():
        line = test_lines[state["idx"]]
        state["idx"] = (state["idx"] + 1) % len(test_lines)
        subtitle.set_text(line, on_typing_done=lambda: QTimer.singleShot(2200, next_line))

    QTimer.singleShot(400, next_line)

    # ── 装配 ──
    crt.set_scene_background(bg)
    crt.set_content(sprite)
    crt.set_overlay(overlay)
    crt.set_hud(hud)
    crt.set_subtitle(subtitle)

    crt.setWindowTitle("Amadeus CRT Demo")
    crt.move(220, 200)
    crt.show()
    crt.play_power_on(800)  # 开机扫描亮线

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
