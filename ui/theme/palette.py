"""统一主题调色板 — 所有 UI 控件的单一颜色来源

为什么存在：
    之前每个自定义绘制控件（bubble / status_hud / emotion_face）各自维护一份
    THEME_COLORS，未接主题的弹窗（plugin_panel /
    startup_screen）干脆写死颜色，导致深浅色模式不一致、甚至深色模式下也各用各的蓝。

本模块是单一真相源，token 严格对齐 ui/theme/light.qss 与 dark.qss：
    dark  : 夜底 #0c0e1c / 月白 #e8ecf5 / 金黄 #e9c46a / 边框 #2a3148 / 表面 #131629
    light : 米白 #f7f6f3 / 墨字 #2c2c2c / 边框 #e5e2db / 表面 #ffffff

自定义绘制（QPainter）控件从这里取色；标准 Qt 控件用 build_qss 生成的样式表。
"""
from __future__ import annotations

from PySide6.QtGui import QColor

# 每个 token 为 RGBA 四元组 (r, g, b, a)
THEME_COLORS = {
    "dark": {
        # 面板 / 容器（对齐 dark.qss 的 #0c0e1c）
        "panel_bg":        (12, 14, 28, 240),
        "panel_border":    (42, 49, 72, 220),
        "panel_overlay":   (255, 255, 255, 10),
        # 卡片（对齐 dark.qss 的 #131629 表面）
        "card_bg":         (19, 22, 41, 235),
        "card_hover":      (255, 255, 255, 14),
        # 文字（对齐 dark.qss）
        "text_primary":    (232, 236, 245, 255),   # #e8ecf5
        "text_secondary":  (163, 172, 194, 255),   # #a3acc2 dim
        "text_muted":      (107, 117, 145, 255),   # #6b7591 mute
        "text_accent_blue":(150, 200, 255, 255),
        # 控件（关闭 / 筛选 tab / 滚动条）
        "close_bg":        (42, 49, 72, 200),
        "close_hover":     (150, 70, 90, 220),     # 危险红，保留
        "tab_bg":          (19, 22, 41, 200),
        "tab_border":      (42, 49, 72, 180),
        "tab_checked":     (90, 120, 210, 220),
        "scroll_bg":       (12, 14, 28, 150),
        "scroll_handle":   (42, 49, 72, 220),
        # gacha reveal 专用
        "gacha_card_bg":   (19, 22, 41, 230),
        "gacha_overlay":   (255, 255, 255, 12),
        "gacha_cell_bg":   (28, 32, 54, 235),
        # plugin panel 专用（对齐 dark.qss）
        "dlg_bg":          (12, 14, 28, 255),
        "dlg_text":        (232, 236, 245, 255),
        "dlg_muted":       (107, 117, 145, 255),
        "input_bg":        (19, 22, 41, 255),
        "input_border":    (42, 49, 72, 255),
        "tree_hover":      (26, 29, 53, 255),
        "tree_selected":   (233, 196, 106, 20),    # 金黄 7%~8% 选中
        "btn_primary":     (74, 144, 217, 255),    # 沿用插件面板蓝
        "btn_primary_hover":(95, 160, 233, 255),
        "btn_send":        (52, 199, 89, 255),     # 沿用发送绿
        "btn_send_hover":  (68, 215, 105, 255),
        "btn_disabled_bg": (26, 29, 53, 255),
        # 强调色（穿透态 / 高亮）—— 统一替代写死的 #cc88ff
        "accent":          (204, 136, 255, 255),   # 紫
        "accent_soft":     (204, 136, 255, 90),    # 半透明紫（边框）
    },
    "light": {
        # 面板 / 容器（对齐 plugin_panel 原浅色 #f7f6f3）
        "panel_bg":        (247, 246, 243, 245),
        "panel_border":    (229, 226, 219, 220),
        "panel_overlay":   (255, 255, 255, 170),
        "card_bg":         (255, 255, 255, 235),
        "card_hover":      (0, 0, 0, 12),
        "text_primary":    (44, 44, 44, 255),
        "text_secondary":  (122, 122, 122, 255),
        "text_muted":      (140, 140, 148, 255),
        "text_accent_blue":(20, 90, 200, 255),
        "close_bg":        (210, 206, 196, 200),
        "close_hover":     (200, 120, 130, 220),
        "tab_bg":          (235, 232, 224, 220),
        "tab_border":      (200, 195, 182, 200),
        "tab_checked":     (90, 130, 210, 230),
        "scroll_bg":       (225, 222, 214, 180),
        "scroll_handle":   (160, 156, 146, 220),
        "gacha_card_bg":   (255, 255, 255, 235),
        "gacha_overlay":   (0, 0, 0, 10),
        "gacha_cell_bg":   (244, 242, 236, 240),
        "dlg_bg":          (247, 246, 243, 255),
        "dlg_text":        (44, 44, 44, 255),
        "dlg_muted":       (122, 122, 122, 255),
        "input_bg":        (255, 255, 255, 255),
        "input_border":    (229, 226, 219, 255),
        "tree_hover":      (247, 246, 243, 255),
        "tree_selected":   (232, 240, 252, 255),  # #e8f0fc
        "btn_primary":     (74, 144, 217, 255),
        "btn_primary_hover":(95, 160, 233, 255),
        "btn_send":        (52, 199, 89, 255),
        "btn_send_hover":  (68, 215, 105, 255),
        "btn_disabled_bg": (229, 226, 219, 255),
        # 强调色（穿透态 / 高亮）—— 统一替代写死的 #cc88ff
        "accent":          (150, 90, 210, 255),    # 紫
        "accent_soft":     (150, 90, 210, 90),     # 半透明紫（边框）
    },
}


def qcolor(theme: str, key: str) -> QColor:
    """取当前主题的某 token 作为 QColor（用于 QPainter 绘制）"""
    return QColor(*THEME_COLORS[theme][key])


def rgba(theme: str, key: str) -> str:
    """返回 'r, g, b, a' 字符串，用于 QSS setStyleSheet 的 rgba(...)"""
    return "%d, %d, %d, %d" % THEME_COLORS[theme][key]


def rgb(theme: str, key: str) -> str:
    """返回 'r, g, b' 字符串，用于 QSS setStyleSheet 的 rgb(...)"""
    r, g, b, _ = THEME_COLORS[theme][key]
    return "%d, %d, %d" % (r, g, b)
