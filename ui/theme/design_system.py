"""玻璃拟态设计系统 — 在基础 QSS(light.qss/dark.qss)之上追加的精修层

目的：
    oc-pet 之前只有一套扁平、圆角偏小的 QSS，浮窗各自写死不透明背景，整体
    缺少「成熟产品」的层次与质感。本模块提供：

    1) 全局标准控件精修：统一更大圆角、聚焦环、Tab/GroupBox 圆润、滚动条更细。
    2) 玻璃容器工具类 #glassPanel / #glassCard：通过 setObjectName("glassPanel")
       套用统一玻璃质感（浮窗容器用）。
    3) apply_glass_shadow()：给浮窗加 QGraphicsDropShadowEffect 软阴影
       （Qt 的 QSS 不支持 box-shadow / backdrop-filter，阴影只能靠代码）。

⚠️ Qt QSS 选择器说明：
    Qt 的 .ClassName 是按 Python 类名匹配的，不存在叫 glass-panel 的类，所以
    不能用 .glass-panel。正确做法是用 objectName 选择器 #glassPanel —— 给容器
    widget 调用 setObjectName("glassPanel")，再 apply_glass_shadow(widget)
    加软阴影即可获得完整玻璃卡效果。

注意：QSS 本身无法做 backdrop-filter 模糊，玻璃感 = 半透明面板背景 + 柔边 +
软阴影（代码）。

所有颜色取自 ui.theme.palette 的 token，深浅色自动对齐。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from ui.theme.palette import rgb, rgba


def build_design_system(theme: str) -> str:
    """返回在基础 QSS 之上追加的设计系统层（主题相关）"""
    panel = rgb(theme, "panel_bg")
    border = rgb(theme, "panel_border")
    accent = rgb(theme, "btn_primary")
    card = rgb(theme, "card_bg")
    card_hover = rgba(theme, "card_hover")
    overlay = rgba(theme, "panel_overlay")
    text_primary = rgb(theme, "text_primary")
    text_secondary = rgb(theme, "text_secondary")
    tab_bg = rgb(theme, "tab_bg")
    sel_bg = accent
    sel_fg = "12, 14, 28" if theme == "dark" else "255, 255, 255"

    return f"""
/* ============ 设计系统精修层（glassmorphism + 统一层级） ============ */

/* 全局选中色统一 */
QWidget {{
    selection-background-color: rgb({sel_bg});
    selection-color: rgb({sel_fg});
}}

/* 按钮：更大圆角 + 聚焦环 */
QPushButton {{
    border-radius: 10px;
}}
QPushButton:focus {{
    outline: none;
    border: 1px solid rgba({accent}, 1.0);
}}

/* 输入聚焦更明显 */
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid rgba({accent}, 1.0);
}}

/* Tab / GroupBox 圆润 */
QTabWidget::pane {{
    border-radius: 12px;
}}
QTabBar::tab {{
    border-radius: 8px;
}}
/* GroupBox 卡片化：统一留白 + 标题层级 + 轻背景，去除朴素堆叠感 */
QGroupBox {{
    border: 1px solid rgba({border}, 0.55);
    border-radius: 12px;
    margin-top: 18px;
    padding: 16px 16px 14px 16px;
    background: rgba({card}, 0.45);
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: rgb({text_primary});
    background: rgba({panel}, 1.0);
}}

/* Tab 栏完整样式：避免只设圆角导致 Tab 被压成不可见 */
QTabWidget::pane {{
    border: 1px solid rgba({border}, 0.8);
    background: rgba({panel}, 0.9);
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    background: rgba({tab_bg}, 0.8);
    color: rgb({text_secondary});
    padding: 8px 18px;
    margin: 2px 4px 0 0;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    background: rgba({panel}, 1.0);
    color: rgb({text_primary});
    border-bottom: 2px solid rgb({accent});
}}
QTabBar::tab:hover {{
    background: rgba({card_hover});
    color: rgb({text_primary});
}}

/* 滑块手柄带描边光晕 */
QSlider::handle:horizontal, QSlider::handle:vertical {{
    border: 2px solid rgba({accent}, 1.0);
}}

/* 滚动条更细更柔 */
QScrollBar:vertical {{
    width: 10px;
}}
QScrollBar:horizontal {{
    height: 10px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    border-radius: 5px;
}}

/* 菜单圆润 */
QMenu {{
    border-radius: 10px;
}}

/* ---------- 玻璃面板工具类（用 setObjectName("glassPanel") 套用） ---------- */
#glassPanel {{
    background: rgba({panel}, 0.82);
    border: 1px solid rgba({border}, 0.9);
    border-radius: 16px;
}}
#glassPanel:hover {{
    border: 1px solid rgba({overlay});
}}

#glassCard {{
    background: rgba({card}, 0.92);
    border: 1px solid rgba({border}, 0.9);
    border-radius: 12px;
}}
#glassCard:hover {{
    background: rgba({card_hover});
}}
"""


def apply_glass_shadow(widget, theme: str = "dark", blur: int = 30, alpha: int = 70):
    """给浮窗加柔和投影，营造玻璃层次。

    widget: 目标控件（通常是浮窗的中央容器或窗口本身）
    注意：窗口需 setAttribute(Qt.WA_TranslucentBackground) 才看得到投影。
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 8)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)
    return shadow
