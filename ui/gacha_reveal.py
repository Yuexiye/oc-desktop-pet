"""盲盒开箱揭晓演出 — 稀有度分级配色、缩放+淡入动画、点击/超时消失

独立顶层窗口（无边框 + 置顶 + 透明背景），居中显示在屏幕中央，
比头顶气泡更隆重，契合「开盲盒」的仪式感。
"""
from PySide6.QtWidgets import QWidget, QLabel, QApplication
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QPainterPath

from ui.theme import get_default, qcolor, rgb, rgba, THEME_COLORS

# 各稀有度的强调色与辉光（accent 为 RGB 三元组，glow 为 RGBA 四元组）
RARITY_THEME = {
    "common":    {"label": "COMMON",    "accent": (182, 190, 202), "glow": (182, 190, 202, 110)},
    "uncommon":  {"label": "UNCOMMON",  "accent": (120, 204, 140), "glow": (120, 204, 140, 130)},
    "rare":      {"label": "RARE",      "accent": (96, 165, 255),  "glow": (96, 165, 255, 160)},
    "epic":      {"label": "EPIC",      "accent": (186, 130, 255), "glow": (186, 130, 255, 170)},
    "legendary": {"label": "LEGENDARY", "accent": (255, 204, 92),  "glow": (255, 204, 92, 200)},
}


class GachaReveal(QWidget):
    """开箱结果揭晓卡片"""

    def __init__(self, icon: str, name: str, rarity_value: str,
                 pity_text: str | None = None, parent=None):
        super().__init__(parent)
        self._icon = icon or "🎁"
        self._name = name or "神秘物品"
        self._theme = RARITY_THEME.get(rarity_value, RARITY_THEME["common"])
        self._pity_text = pity_text
        self._dismissed = False
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        if mgr is not None:
            mgr.theme_changed.connect(self.set_theme)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(260, 320)

        self._build_labels()
        self._center_on_screen()
        self._animate_in()

        # 预创建淡出动画（dismiss 复用）；不在 __init__ 启动时窗口保持不透明。
        # gacha_mixin 会在 show() 之后立即连接 _op_out.finished，故必须先存在。
        self._op_out = QPropertyAnimation(self, b"windowOpacity")
        self._op_out.setDuration(260)
        self._op_out.setStartValue(self.windowOpacity())
        self._op_out.setEndValue(0.0)
        self._op_out.finished.connect(self.close)

        # 自动消失
        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.timeout.connect(self.dismiss)
        self._auto.start(4000)

    def _build_labels(self):
        r = self._theme
        accent_css = "rgb(%d, %d, %d)" % r["accent"]

        # 稀有度标签
        self._lbl_rarity = QLabel(r["label"], self)
        self._lbl_rarity.setAlignment(Qt.AlignCenter)
        self._lbl_rarity.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self._lbl_rarity.setStyleSheet(f"color: {accent_css}; background: transparent;")
        self._lbl_rarity.setGeometry(0, 24, 260, 22)

        # 物品图标（emoji）
        self._lbl_icon = QLabel(self._icon, self)
        self._lbl_icon.setAlignment(Qt.AlignCenter)
        self._lbl_icon.setFont(QFont("Segoe UI Emoji", 96))
        self._lbl_icon.setStyleSheet("background: transparent;")
        self._lbl_icon.setGeometry(0, 68, 260, 150)

        # 物品名称
        self._lbl_name = QLabel(self._name, self)
        self._lbl_name.setAlignment(Qt.AlignCenter)
        self._lbl_name.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        self._lbl_name.setGeometry(12, 232, 236, 30)

        # 保底进度脚注（可选）
        if self._pity_text:
            self._lbl_pity = QLabel(self._pity_text, self)
            self._lbl_pity.setAlignment(Qt.AlignCenter)
            self._lbl_pity.setFont(QFont("Microsoft YaHei UI", 11))
            self._lbl_pity.setGeometry(0, 268, 260, 22)

        self._apply_styles()

    def _apply_styles(self):
        """按主题重设文字颜色（稀有度强调色保持各自配色）"""
        t = self._ui_theme
        self._lbl_name.setStyleSheet(
            f"color: rgb({rgb(t, 'text_primary')}); background: transparent;")
        if getattr(self, "_lbl_pity", None):
            self._lbl_pity.setStyleSheet(
                f"color: rgba({rgba(t, 'text_secondary')}); background: transparent;")

    def set_theme(self, theme: str):
        """主题切换 — 由 ThemeManager.theme_changed 信号触发"""
        if theme not in THEME_COLORS:
            return
        if theme == self._ui_theme:
            return
        self._ui_theme = theme
        self._apply_styles()
        self.update()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def _animate_in(self):
        full = self.geometry()
        cx, cy = full.center().x(), full.center().y()
        w, h = full.width(), full.height()
        # 从 60% 尺寸弹出
        sw, sh = int(w * 6 / 10), int(h * 6 / 10)
        small = QRect(cx - sw // 2, cy - sh // 2, sw, sh)
        self.setGeometry(small)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        # 淡入
        self._op = QPropertyAnimation(self, b"windowOpacity")
        self._op.setDuration(280)
        self._op.setStartValue(0.0)
        self._op.setEndValue(1.0)
        self._op.setEasingCurve(QEasingCurve.OutCubic)

        # 缩放弹出（轻微过冲，更俏皮）
        self._geo = QPropertyAnimation(self, b"geometry")
        self._geo.setDuration(420)
        self._geo.setStartValue(small)
        self._geo.setEndValue(full)
        self._geo.setEasingCurve(QEasingCurve.OutBack)

        self._op.start()
        self._geo.start()

    def dismiss(self):
        """淡出并关闭"""
        if self._dismissed:
            return
        self._dismissed = True
        self._auto.stop()
        self._op_out.start()

    def mousePressEvent(self, event):
        self.dismiss()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(*self._theme["accent"])
        glow = QColor(*self._theme["glow"])
        w, h = self.width(), self.height()
        margin = 18
        card = QRect(margin, margin, w - margin * 2, h - margin * 2)
        radius = 24

        # 外发光（多层描边模拟辉光）
        for i, alpha in enumerate((40, 80, 130)):
            gp = QPainterPath()
            inset = 6 + i * 5
            gp.addRoundedRect(card.adjusted(-inset, -inset, inset, inset),
                              radius + inset, radius + inset)
            c = QColor(glow)
            c.setAlpha(alpha)
            p.fillPath(gp, c)

        # 卡片底色（深色玻璃）
        base = QPainterPath()
        base.addRoundedRect(card, radius, radius)
        p.fillPath(base, QColor(18, 22, 40, 225))
        # 顶部高光
        p.fillPath(base, QColor(255, 255, 255, 12))

        # 稀有度描边
        pen = QPen(accent)
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRoundedRect(card, radius, radius)
        p.end()


# 稀有度排序（用于十连取最佳）
_RARITY_ORDER = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}


class GachaRevealMulti(QWidget):
    """十连抽揭晓 — 5×2 网格卡片，错峰弹出，最佳稀有度配色 + 音效

    适合一次抽多个结果的仪式感展示；点击或 6 秒后自动消失。
    """

    def __init__(self, items: list, pity_text: str | None = None, parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._pity_text = pity_text
        self._dismissed = False
        self._icon_labels: list[QLabel] = []
        self._name_labels: list[QLabel] = []
        self._cell_rects: list[QRect] = []

        # 取最佳稀有度决定整体配色
        def _ord(it):
            return _RARITY_ORDER.get(getattr(it.rarity, "value", "common"), 0)
        best = max(self._items, key=_ord) if self._items else None
        best_rv = getattr(best.rarity, "value", "common") if best else "common"
        self._best_theme = RARITY_THEME.get(best_rv, RARITY_THEME["common"])

        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        if mgr is not None:
            mgr.theme_changed.connect(self.set_theme)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(460, 500)

        self._build_header()
        self._build_cells()
        self._center_on_screen()
        self._animate_in()

        # 预创建淡出动画（dismiss 复用）；gacha_mixin 在 show() 后会立即连接
        # _op_out.finished，故必须先存在，避免 AttributeError 导致演出降级。
        self._op_out = QPropertyAnimation(self, b"windowOpacity")
        self._op_out.setDuration(260)
        self._op_out.setStartValue(self.windowOpacity())
        self._op_out.setEndValue(0.0)
        self._op_out.finished.connect(self.close)

        try:
            from ui.gacha_sound import play_reveal
            play_reveal(best_rv)
        except Exception:
            logger.exception("十连音效播放失败")

        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.timeout.connect(self.dismiss)
        self._auto.start(6000)

    def _build_header(self):
        accent_css = "rgb(%d, %d, %d)" % self._best_theme["accent"]
        self._lbl_title = QLabel("✨ 十连抽 ✨", self)
        self._lbl_title.setAlignment(Qt.AlignCenter)
        self._lbl_title.setFont(QFont("Microsoft YaHei UI", 18, QFont.Bold))
        self._lbl_title.setStyleSheet(
            f"color: {accent_css}; background: transparent;")
        self._lbl_title.setGeometry(0, 20, 460, 28)

    def _build_cells(self):
        cols, cell = 5, 78
        gap = 12
        grid_w = cols * cell + (cols - 1) * gap
        x0 = (self.width() - grid_w) // 2
        y0 = 70
        for idx, it in enumerate(self._items[:10]):
            r, c = divmod(idx, cols)
            x = x0 + c * (cell + gap)
            y = y0 + r * (cell + gap)
            rect = QRect(x, y, cell, cell)
            self._cell_rects.append(rect)

            rv = getattr(it.rarity, "value", "common")
            theme = RARITY_THEME.get(rv, RARITY_THEME["common"])
            icon = QLabel(it.icon or "🎁", self)
            icon.setAlignment(Qt.AlignCenter)
            icon.setFont(QFont("Segoe UI Emoji", 40))
            icon.setStyleSheet("background: transparent;")
            icon.setGeometry(x, y + 4, cell, cell - 22)
            self._icon_labels.append(icon)

            nm = QLabel(it.name or "?", self)
            nm.setAlignment(Qt.AlignCenter)
            nm.setFont(QFont("Microsoft YaHei UI", 9))
            nm.setStyleSheet(
                f"color: rgb({theme['accent'][0]},{theme['accent'][1]},"
                f"{theme['accent'][2]}); background: transparent;")
            nm.setGeometry(x, y + cell - 20, cell, 16)
            self._name_labels.append(nm)

        # 保底脚注
        if self._pity_text:
            self._lbl_pity = QLabel(self._pity_text, self)
            self._lbl_pity.setAlignment(Qt.AlignCenter)
            self._lbl_pity.setFont(QFont("Microsoft YaHei UI", 12))
            self._lbl_pity.setGeometry(0, self.height() - 40, self.width(), 24)
            self._lbl_pity.setStyleSheet(
                f"color: rgba({rgba(self._ui_theme, 'text_secondary')}); background: transparent;")

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def _animate_in(self):
        full = self.geometry()
        cx, cy = full.center().x(), full.center().y()
        w, h = full.width(), full.height()
        sw, sh = int(w * 7 / 10), int(h * 7 / 10)
        small = QRect(cx - sw // 2, cy - sh // 2, sw, sh)
        self.setGeometry(small)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._op = QPropertyAnimation(self, b"windowOpacity")
        self._op.setDuration(300)
        self._op.setStartValue(0.0)
        self._op.setEndValue(1.0)
        self._op.setEasingCurve(QEasingCurve.OutCubic)
        self._op.start()

        # 窗口整体缩放弹出
        self._geo = QPropertyAnimation(self, b"geometry")
        self._geo.setDuration(420)
        self._geo.setStartValue(small)
        self._geo.setEndValue(full)
        self._geo.setEasingCurve(QEasingCurve.OutBack)
        self._geo.start()

        # 卡片错峰弹出（每个延迟 70ms）
        for i, (lbl, rect) in enumerate(zip(self._icon_labels, self._cell_rects)):
            base = QRect(rect.x() + rect.width() // 2 - 6,
                         rect.y() + rect.height() // 2 - 6, 12, 12)
            lbl.setGeometry(base)
            anim = QPropertyAnimation(lbl, b"geometry")
            anim.setDuration(340)
            anim.setStartValue(base)
            anim.setEndValue(rect)
            anim.setEasingCurve(QEasingCurve.OutBack)
            QTimer.singleShot(i * 70, anim.start)

    def set_theme(self, theme: str):
        """主题切换 — 由 ThemeManager.theme_changed 信号触发"""
        if theme not in THEME_COLORS:
            return
        if theme == self._ui_theme:
            return
        self._ui_theme = theme
        if getattr(self, "_lbl_pity", None):
            self._lbl_pity.setStyleSheet(
                f"color: rgba({rgba(theme, 'text_secondary')}); background: transparent;")
        self.update()

    def dismiss(self):
        if self._dismissed:
            return
        self._dismissed = True
        self._auto.stop()
        self._op_out.start()

    def mousePressEvent(self, event):
        self.dismiss()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin = 16
        card = QRect(margin, margin, w - margin * 2, h - margin * 2)
        radius = 26

        # 外发光（最佳稀有度）
        glow = QColor(*self._best_theme["glow"])
        for i, alpha in enumerate((40, 80, 130)):
            gp = QPainterPath()
            inset = 6 + i * 5
            gp.addRoundedRect(card.adjusted(-inset, -inset, inset, inset),
                              radius + inset, radius + inset)
            c = QColor(glow)
            c.setAlpha(alpha)
            p.fillPath(gp, c)

        # 卡片底色
        base = QPainterPath()
        base.addRoundedRect(card, radius, radius)
        p.fillPath(base, qcolor(self._ui_theme, "gacha_card_bg"))
        p.fillPath(base, qcolor(self._ui_theme, "gacha_overlay"))

        # 单元格框（按各自稀有度配色）
        for rect, it in zip(self._cell_rects, self._items[:10]):
            rv = getattr(it.rarity, "value", "common")
            theme = RARITY_THEME.get(rv, RARITY_THEME["common"])
            accent = QColor(*theme["accent"])
            cell_path = QPainterPath()
            cell_path.addRoundedRect(rect, 14, 14)
            p.fillPath(cell_path, qcolor(self._ui_theme, "gacha_cell_bg"))
            pen = QPen(accent)
            pen.setWidth(1)
            p.setPen(pen)
            p.drawRoundedRect(rect, 14, 14)

        # 顶部描边
        pen = QPen(QColor(*self._best_theme["accent"]))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRoundedRect(card, radius, radius)
        p.end()


__all__ = ["GachaReveal", "GachaRevealMulti", "RARITY_THEME"]
