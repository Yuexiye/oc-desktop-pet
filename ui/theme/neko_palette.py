"""N.E.K.O. 设计语言 token — 单一数据源（P0-6）

从 ``third_party_reference/neko/frontend/react-neko-chat/src/styles.css``
提取的设计 token（配色/圆角/间距），供：
- ``ui/theme/neko.qss`` 生成样式（QSS 文件内以色值字面量使用）；
- Python 绘制侧（头像渐变、focus glow 辉光、thinking dots）取色。

结构与 styles.css 对齐：
  - bubble 角色色：user（浅蓝渐变）/ assistant（白）/ system（灰 chip）
  - avatar 角色色：assistant（蓝渐变）/ user（浅灰蓝渐变）
  - focus glow：soft blue ``rgba(150,195,255,*)`` / ``rgba(130,180,255,*)``
  - 圆角：user 16/8/16/16，assistant 8/16/16/16，system chip 999px(胶囊)
  - 间距：消息间隔 12px、气泡内边距 9x11、meta 字号 0.72rem 级

light/dark 双主题；``focus_glow`` 存 RGB 元组供 QRadialGradient/QPen 使用。
"""

# RGB 元组 / 十六进制 / rgba 字符串混合；QSS 中直接写十六进制与 rgba()。
NEKO_PALETTE: dict[str, dict[str, object]] = {
    "light": {
        # 表面
        "surface_bg": "#f6f8fc",
        "panel_bg": "rgba(255,255,255,0.91)",
        "text": "#1f2329",
        "text_secondary": "#7e899a",
        "text_author": "#596579",
        "divider": "rgba(111,129,158,0.16)",
        # 气泡
        "bubble_user_bg": "#b9d6ff",
        "bubble_user_bg2": "#d4e7ff",
        "bubble_user_text": "#142033",
        "bubble_assistant_bg": "#ffffff",
        "bubble_assistant_text": "#273042",
        "bubble_system_bg": "rgba(131,148,175,0.14)",
        "bubble_system_text": "#607086",
        "bubble_shadow": "rgba(53,72,104,0.05)",
        # 头像
        "avatar_user_bg": "#e6edf8",
        "avatar_user_bg2": "#d8e2f2",
        "avatar_user_text": "#1f2329",
        "avatar_assistant_bg": "#46a0ff",
        "avatar_assistant_bg2": "#2f69df",
        "avatar_assistant_text": "#ffffff",
        # 滚动条 / chip
        "scroll_thumb": "rgba(140,152,172,0.38)",
        "chip_bg": "rgba(131,148,175,0.14)",
        "chip_text": "#607086",
        # 输入 / 分隔
        "input_border": "rgba(127,144,170,0.25)",
        "input_focus_border": "rgba(64,197,241,0.55)",
        # 专注辉光（RGB 元组，Python 绘制用）
        "focus_glow": (150, 195, 255),
        "focus_glow_deep": (130, 180, 255),
        # 记忆面板卡片强调色
        "card_event": "#2f68df",
        "card_scene": "#7d5fc4",
        "card_fact": "#236845",
        "card_bg": "#ffffff",
        "card_text": "#273042",
        "card_meta": "#7e899a",
        # HUD（P1-8 状态面板 / Amadeus 重上色）
        "hud_panel_bg": "rgba(255,255,255,0.91)",
        "hud_panel_border": "rgba(53,72,104,0.16)",
        "hud_title": "#1f2329",
        "hud_text": "#596579",
        "hud_text_secondary": "#7e899a",
        "hud_track": "rgba(111,129,158,0.16)",
        "hud_bar_good": "#3aa876",
        "hud_bar_warn": "#d9a03c",
        "hud_bar_bad": "#d95f5f",
        "hud_emotion_happy": "#78d296",
        "hud_emotion_sad": "#82aae3",
        "hud_emotion_thinking": "#c8b48c",
        "hud_emotion_surprised": "#f0c878",
        "hud_emotion_angry": "#eb7878",
        "hud_emotion_neutral": "#b4becd",
        "hud_dot_running": "#2f68df",
        "hud_dot_needyou": "#d9a03c",
        "hud_dot_active": "#40a0e8",
        "hud_dot_idle": "#8a94a6",
        "hud_glow_text": "#2f68df",
        "hud_border_glow": "rgba(47,104,223,0.45)",
    },
    "dark": {
        # 表面（oc-pet dark 基底：夜蓝紫）
        "surface_bg": "#161a22",
        "panel_bg": "rgba(30,34,43,0.92)",
        "text": "#e8ecf5",
        "text_secondary": "#8a94a6",
        "text_author": "#a6b0c2",
        "divider": "rgba(148,166,196,0.14)",
        # 气泡
        "bubble_user_bg": "#2c4a7a",
        "bubble_user_bg2": "#3a5f9e",
        "bubble_user_text": "#eaf1ff",
        "bubble_assistant_bg": "#232833",
        "bubble_assistant_text": "#e8ecf5",
        "bubble_system_bg": "rgba(120,140,170,0.16)",
        "bubble_system_text": "#9aa6ba",
        "bubble_shadow": "rgba(0,0,0,0.25)",
        # 头像
        "avatar_user_bg": "#3a4256",
        "avatar_user_bg2": "#4b5570",
        "avatar_user_text": "#eef2fa",
        "avatar_assistant_bg": "#3a72e8",
        "avatar_assistant_bg2": "#6aa1ff",
        "avatar_assistant_text": "#ffffff",
        # 滚动条 / chip
        "scroll_thumb": "rgba(148,166,196,0.35)",
        "chip_bg": "rgba(120,140,170,0.16)",
        "chip_text": "#9aa6ba",
        # 输入 / 分隔
        "input_border": "rgba(148,166,196,0.22)",
        "input_focus_border": "rgba(68,183,254,0.55)",
        # 专注辉光（暗色下略降亮度、提高对比）
        "focus_glow": (110, 150, 220),
        "focus_glow_deep": (90, 130, 205),
        # 记忆面板卡片强调色
        "card_event": "#6aa1ff",
        "card_scene": "#9d82e0",
        "card_fact": "#4caf7d",
        "card_bg": "#232833",
        "card_text": "#e8ecf5",
        "card_meta": "#8a94a6",
        # HUD（P1-8 状态面板 / Amadeus 重上色）
        "hud_panel_bg": "rgba(30,34,43,0.92)",
        "hud_panel_border": "rgba(148,166,196,0.18)",
        "hud_title": "#e8ecf5",
        "hud_text": "#a6b0c2",
        "hud_text_secondary": "#8a94a6",
        "hud_track": "rgba(148,166,196,0.16)",
        "hud_bar_good": "#4caf7d",
        "hud_bar_warn": "#e6b45a",
        "hud_bar_bad": "#e06a6a",
        "hud_emotion_happy": "#8ae3a8",
        "hud_emotion_sad": "#93b8f2",
        "hud_emotion_thinking": "#d4c09a",
        "hud_emotion_surprised": "#f7d58c",
        "hud_emotion_angry": "#f08a8a",
        "hud_emotion_neutral": "#c4cdda",
        "hud_dot_running": "#6aa1ff",
        "hud_dot_needyou": "#e6b45a",
        "hud_dot_active": "#5fc0f0",
        "hud_dot_idle": "#6b7591",
        "hud_glow_text": "#6aa1ff",
        "hud_border_glow": "rgba(106,161,255,0.45)",
    },
}

# 布局 token（HUD / 角色卡共用，主题无关；圆角/间距不散落硬编码）
NEKO_LAYOUT: dict[str, int] = {
    # StatusHUD
    "hud_radius": 16,
    "hud_pad": 12,
    "hud_row_h": 22,
    "hud_bar_h": 7,
    "hud_bar_radius": 3,
    "hud_emo_row_h": 20,
    # AmadeusHUD
    "amadeus_radius": 9,
    "amadeus_pad_x": 12,
    "amadeus_pad_y": 7,
    "amadeus_dot_r": 4,
    "amadeus_gap": 14,
    "amadeus_pulse_ms": 500,
}


def palette(theme: str) -> dict[str, object]:
    """取某主题的 token 字典；未知主题回退 light。"""
    return NEKO_PALETTE.get(theme, NEKO_PALETTE["light"])


def glow_rgb(theme: str, deep: bool = False) -> tuple[int, int, int]:
    """取 focus glow RGB 元组（QPainter 用）。"""
    p = palette(theme)
    return tuple(p["focus_glow_deep" if deep else "focus_glow"])  # type: ignore[return-value]


def neko_qcolor(theme: str, key: str):
    """把 token（hex / rgba() 字符串 / RGB 元组）解析为 QColor（QPainter 用）。

    QSS 侧直接写色值字面量；Python 绘制侧（StatusHUD / AmadeusHUD / 角色卡
    头像裁切等）统一经此函数取色，保证与 ``neko.qss`` 同一数据源。
    未知 key / 解析失败回退黑色，绝不抛异常。
    """
    from PySide6.QtGui import QColor

    value = palette(theme).get(key, "#000000")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("rgba"):
            inner = text[text.find("(") + 1:text.rfind(")")]
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) >= 3:
                try:
                    r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                    alpha = float(parts[3]) if len(parts) > 3 else 1.0
                    return QColor(r, g, b, max(0, min(255, int(alpha * 255))))
                except (TypeError, ValueError):
                    return QColor("#000000")
        return QColor(text)
    if isinstance(value, (tuple, list)):
        try:
            return QColor(*value)
        except (TypeError, ValueError):
            return QColor("#000000")
    return QColor("#000000")
