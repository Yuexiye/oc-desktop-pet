"""物品图鉴收集册 — 按稀有度分组展示全部虚拟收集物

已收集：彩色图标 + 名称 + 稀有度辉光；未收集：灰化锁图标 + "???"。
顶部显示收集进度 X / N，稀有度筛选 tab。玻璃卡 + 稀有度描边 + hover 高亮。
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (QWidget, QLabel, QScrollArea, QGridLayout,
                               QPushButton, QButtonGroup, QApplication)
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen, QCursor, QFont

from ui.gacha_reveal import RARITY_THEME
from ui.theme import get_default, qcolor, rgb, rgba, THEME_COLORS

logger = logging.getLogger(__name__)

_RARITY_ORDER = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}
_RARITY_LABEL = {
    "common": "普通", "uncommon": "优秀", "rare": "稀有",
    "epic": "史诗", "legendary": "传说",
}
_GREY = (120, 124, 138)


class CollectCard(QWidget):
    """单张收集物卡片（图标 + 名称 + 稀有度描边），hover 高亮"""

    def __init__(self, item, collected: bool, parent=None):
        super().__init__(parent)
        self._item = item
        self._collected = collected
        rv = getattr(item.rarity, "value", "common")
        self._theme = RARITY_THEME.get(rv, RARITY_THEME["common"])
        self._hover = False
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        self.setFixedSize(104, 116)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        self._icon = QLabel(self)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setFont(QFont("Segoe UI Emoji", 42))
        self._icon.setStyleSheet("background: transparent;")
        self._icon.setGeometry(0, 8, 104, 64)
        self._icon.setText(item.icon if collected else "🔒")

        self._name = QLabel(self)
        self._name.setAlignment(Qt.AlignCenter)
        self._name.setFont(QFont("Microsoft YaHei UI", 10))
        ac = self._theme["accent"]
        if collected:
            name_css = f"color: rgb({ac[0]},{ac[1]},{ac[2]}); background: transparent;"
        else:
            name_css = f"color: rgb({rgb(self._ui_theme, 'text_muted')}); background: transparent;"
        self._name.setStyleSheet(name_css)
        self._name.setGeometry(2, 74, 100, 18)
        self._name.setText(item.name if collected else "???")

        self._rar = QLabel(self._theme["label"], self)
        self._rar.setAlignment(Qt.AlignCenter)
        self._rar.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
        if collected:
            rar_css = f"color: rgb({ac[0]},{ac[1]},{ac[2]}); background: transparent;"
        else:
            rar_css = f"color: rgb({rgb(self._ui_theme, 'text_muted')}); background: transparent;"
        self._rar.setStyleSheet(rar_css)
        self._rar.setGeometry(0, 94, 104, 16)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRect(4, 4, self.width() - 8, self.height() - 8)
        radius = 14
        accent = QColor(*(self._theme["accent"] if self._collected else _GREY))
        # 底色
        base = QPainterPath()
        base.addRoundedRect(rect, radius, radius)
        p.fillPath(base, qcolor(self._ui_theme, "card_bg"))
        if self._hover and self._collected:
            p.fillPath(base, qcolor(self._ui_theme, "card_hover"))
        # 描边（hover 加粗）
        pen = QPen(accent)
        pen.setWidth(2 if self._hover else 1)
        p.setPen(pen)
        p.drawRoundedRect(rect, radius, radius)
        p.end()


class CollectionBook(QWidget):
    """收集册主窗口（可拖拽、筛选、滚动）"""

    def __init__(self, items: list, collected: set[str], parent=None):
        super().__init__(parent)
        self._all = [it for it in items if getattr(it, "item_type", "") == "item"]
        self._collected = collected
        self._filter = "all"
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(560, 620)
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        self._build_ui()
        self._rebuild()
        self._apply_styles()
        self._center_on_screen()
        self.setWindowOpacity(0.0)
        if mgr is not None:
            mgr.theme_changed.connect(self.set_theme)

    def _build_ui(self):
        # 标题栏
        self._title = QLabel("📖 收藏图鉴", self)
        self._title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        self._title.setGeometry(18, 14, 220, 26)

        self._progress = QLabel("", self)
        self._progress.setFont(QFont("Microsoft YaHei UI", 11))
        self._progress.setGeometry(18, 42, 300, 20)

        self._close = QPushButton("×", self)
        self._close.setFixedSize(28, 28)
        self._close.setFont(QFont("Microsoft YaHei UI", 12))
        self._close.clicked.connect(self.close)
        self._close.move(self.width() - 40, 14)

        # 筛选 tab
        self._tabs = QButtonGroup(self)
        self._tabs.setExclusive(True)
        rarities = [rv for rv in _RARITY_ORDER
                    if any(getattr(it.rarity, "value", "") == rv for it in self._all)]
        labels = [("all", "全部")] + [(rv, _RARITY_LABEL[rv]) for rv in rarities]
        x = 18
        for rv, text in labels:
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            btn.setFixedSize(64, 26)
            btn.setFont(QFont("Microsoft YaHei UI", 10))
            btn.move(x, 72)
            self._tabs.addButton(btn)
            btn.clicked.connect(lambda _=False, r=rv: self._on_filter(r))
            x += 72
        # 默认选中「全部」
        first = self._tabs.buttons()[0]
        first.setChecked(True)

        # 滚动区
        self._scroll = QScrollArea(self)
        self._scroll.setGeometry(12, 108, self.width() - 24, self.height() - 120)
        self._scroll.setWidgetResizable(True)

        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background:transparent;")
        self._scroll.setWidget(self._grid_widget)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(14)
        self._grid.setContentsMargins(10, 10, 10, 10)

    def _apply_styles(self):
        """按当前主题重设标题/进度/关闭/筛选 tab/滚动条样式"""
        t = self._ui_theme
        self._title.setStyleSheet(
            f"color: rgb({rgb(t, 'text_primary')}); background: transparent;")
        self._progress.setStyleSheet(
            f"color: rgb({rgb(t, 'text_accent_blue')}); background: transparent;")
        self._close.setStyleSheet(
            f"QPushButton{{font-family:'Microsoft YaHei';font-size:12px;"
            f"color:rgb({rgb(t, 'text_primary')});background:rgba({rgba(t, 'close_bg')});"
            f"border-radius:14px;}} "
            f"QPushButton:hover{{background:rgba({rgba(t, 'close_hover')});}}")
        tab_base = (
            f"QPushButton{{font-family:'Microsoft YaHei';font-size:10px;"
            f"color:rgb({rgb(t, 'text_secondary')});background:rgba({rgba(t, 'tab_bg')});"
            f"border-radius:13px;border:1px solid rgba({rgba(t, 'tab_border')});}}"
            f"QPushButton:checked{{color:rgb({rgb(t, 'text_primary')});"
            f"background:rgba({rgba(t, 'tab_checked')});}}"
        )
        for btn in self._tabs.buttons():
            btn.setStyleSheet(tab_base)
        self._scroll.setStyleSheet(
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QScrollBar:vertical{{background:rgba({rgba(t, 'scroll_bg')});width:8px;"
            f"border-radius:4px;}} QScrollBar::handle:vertical{{background:rgba({rgba(t, 'scroll_handle')});"
            f"border-radius:4px;}}")

    def set_theme(self, theme: str):
        """主题切换 — 由 ThemeManager.theme_changed 信号触发"""
        if theme not in THEME_COLORS:
            return
        if theme == self._ui_theme:
            return
        self._ui_theme = theme
        self._apply_styles()
        self._rebuild()   # 卡片在 _rebuild 内重建，自动读取新主题
        self.update()

    def _on_filter(self, rv: str):
        self._filter = rv
        self._rebuild()

    def _rebuild(self):
        # 清空旧卡片
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = [it for it in self._all
                 if self._filter == "all"
                 or getattr(it.rarity, "value", "") == self._filter]
        collected_n = sum(1 for it in items
                          if getattr(it, "item_id", None) in self._collected)
        total_n = len(items)
        pct = int(100 * collected_n / total_n) if total_n else 0
        # 标题栏进度（按全部）
        all_n = len(self._all)
        all_c = sum(1 for it in self._all
                    if getattr(it, "item_id", None) in self._collected)
        all_pct = int(100 * all_c / all_n) if all_n else 0
        self._progress.setText(f"已收集 {all_c} / {all_n}（{all_pct}%）")

        for idx, it in enumerate(items):
            cid = getattr(it, "item_id", None)
            card = CollectCard(it, cid in self._collected)
            self._grid.addWidget(card, idx // 4, idx % 4)

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.x() + (geo.width() - self.width()) // 2,
                  geo.y() + (geo.height() - self.height()) // 2)

    def showEvent(self, event):
        super().showEvent(event)
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        self._op = QPropertyAnimation(self, b"windowOpacity")
        self._op.setDuration(260)
        self._op.setStartValue(0.0)
        self._op.setEndValue(1.0)
        self._op.setEasingCurve(QEasingCurve.OutCubic)
        self._op.start()

    def mousePressEvent(self, event):
        if event.y() < 100 and event.x() < self.width() - 44:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRect(6, 6, self.width() - 12, self.height() - 12)
        radius = 22
        base = QPainterPath()
        base.addRoundedRect(rect, radius, radius)
        p.fillPath(base, qcolor(self._ui_theme, "panel_bg"))
        p.fillPath(base, qcolor(self._ui_theme, "panel_overlay"))
        pen = QPen(qcolor(self._ui_theme, "panel_border"))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawRoundedRect(rect, radius, radius)
        p.end()


__all__ = ["CollectionBook"]
