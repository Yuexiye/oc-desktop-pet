"""对话气泡组件 — 主题感知 + 淡入动画 + 打字机效果 + 表情贴图

颜色从 ThemeManager 拉，不再硬编码。
- 普通文本：emoji 以"贴图"尺寸（1.5x）渲染，与文字混排自动换行。
- set_sticker：大表情贴图模式（如摸头大反应的 💕），居中玻璃卡 + 可选文案。
"""
import logging
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRect, Signal
from PySide6.QtGui import QPainter, QFont, QColor, QPainterPath, QFontMetrics, QPixmap

from ui.theme import get_default

logger = logging.getLogger(__name__)


# 主题色字典（与 ui/theme/light.qss 和 dark.qss 对齐）
THEME_COLORS = {
    "light": {
        "bg": (255, 246, 235, 240),        # 米色透明（暖深底上的气泡）
        "text": (60, 40, 25, 255),          # 暖深棕字
        "bright_bg": (255, 220, 180, 245),  # 沙橙高亮
        "bright_text": (80, 40, 10, 255),   # 深橙字
        "shadow": (0, 0, 0, 40),
    },
    "dark": {
        "bg": (20, 24, 50, 230),            # 夜蓝紫透明
        "text": (232, 236, 245, 255),       # 月白
        "bright_bg": (233, 196, 106, 235),  # 金黄高亮
        "bright_text": (12, 14, 28, 255),   # 夜底字
        "shadow": (0, 0, 0, 80),
    },
}

# emoji 命中区间（含 ZWJ / 变体选择符以合并成连续表情）
_EMOJI_RANGES = (
    (0x2300, 0x23FF),   # 杂项技术符号
    (0x2600, 0x27BF),   # 杂项符号 / 装饰
    (0x2B00, 0x2BFF),   # 杂项符号和箭头
    (0x1F000, 0x1FAFF), # 表情符号补充
)


def _is_emoji(ch: str) -> bool:
    """判断单个字符是否应作为表情贴图渲染（含 ZWJ/FE0F 以合并连写）"""
    if ch in ("\u200D", "\uFE0F"):
        return True
    o = ord(ch)
    for lo, hi in _EMOJI_RANGES:
        if lo <= o <= hi:
            return True
    return False


class ChatBubble(QWidget):
    """头顶对话气泡 — 主题感知 + 淡入 + 打字机 + 三角指针 + 表情贴图"""

    theme_changed = Signal()  # 主题切换时通知外部（pet.py 重绘）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._full_text = ""
        self._typewriter_revealed = 0
        self._is_typing = False
        self._on_typing_done = None
        self._typewriter_speed = 28
        self._padding_h = 14
        self._padding_v = 10
        self._max_width = 260  # 适配 300px 窗口，气泡可更宽更舒适
        self._theme = "light"  # 默认 light
        self._bg_color = self._color("bg")
        self._text_color = self._color("text")
        self._shadow_color = self._color("shadow")
        self._font = QFont("Microsoft YaHei UI", 10)
        self.setFont(self._font)
        self.setMinimumSize(40, 30)

        # 贴图模式
        self._sticker_mode = False
        self._sticker_emoji = ""
        self._sticker_caption = ""
        self._sticker_image: QPixmap | None = None

        # 富文本布局缓存
        self._rich_lines = []  # list of (segments, line_width, line_height)

        # 连接全局 ThemeManager（如果已初始化）
        mgr = get_default()
        if mgr is not None:
            self._theme = mgr.current
            self._bg_color = self._color("bg")
            self._text_color = self._color("text")
            self._shadow_color = self._color("shadow")
            mgr.theme_changed.connect(self.set_theme)

        # 淡入动画：不再用 QPropertyAnimation(windowOpacity)。
        # ChatBubble 是 PetWindow 的子 widget（非顶层窗口），Qt 明确
        # 规定 setWindowOpacity 只在顶层窗口生效，子控件上该调用是
        # 静默 no-op。结果：_fade_anim.start() 之后窗口透明度停留在
        # setWindowOpacity(0.0) 设置值上——但因 setWindowOpacity 在子控件
        # 上无效，气泡的透明度实际上根本没变；然而一旦动画把 windowOpacity
        # 从 0.0 起跳插值到 1.0，Qt 内部对子控件的 opacity 缓存路径
        # 可能进入不一致状态（部分平台表现成完全不显示）。
        # 改成在 paintEvent 里对 _bg_color 应用 alpha 系数实现淡入，
        # 不依赖任何 window-opacity API，子控件/顶层窗口都有效。
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_steps = 0
        self._fade_total = 11          # 16*11 ≈ 176ms
        self._fade_alpha = 1.0         # 0.0~1.0，1.0=完全显示

        # 打字机时钟
        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.timeout.connect(self._typewriter_tick)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._flash_tick)
        self._flash_count = 0
        self._bright = False
        self.hide()

    def _color(self, key: str) -> QColor:
        """取当前主题的颜色（QColor 形式）"""
        rgba = THEME_COLORS[self._theme][key]
        return QColor(*rgba)

    def set_theme(self, theme: str):
        """切换主题 — 由 ThemeManager.theme_changed 信号触发"""
        if theme not in THEME_COLORS:
            return
        if theme == self._theme:
            return
        self._theme = theme
        if self._bright:
            self._bg_color = self._color("bright_bg")
            self._text_color = self._color("bright_text")
        else:
            self._bg_color = self._color("bg")
            self._text_color = self._color("text")
        self._shadow_color = self._color("shadow")
        self.theme_changed.emit()
        self.update()

    @property
    def theme(self) -> str:
        return self._theme

    # ── 公共 API ──

    def set_text(self, text: str, bright: bool = False, on_typing_done=None):
        """设置文字并开始打字机效果
        
        Args:
            text: 完整文本（emoji 会以贴图尺寸渲染）
            bright: 高亮模式（emotion == "happy" 时）
            on_typing_done: 打字完成回调
        """
        self._sticker_mode = False
        self._typewriter_timer.stop()
        self._is_typing = False
        
        # UI优化: 停止流式打字光标
        self._stop_cursor_blink()

        self._full_text = text
        self._text = text
        self._typewriter_revealed = 0
        self._on_typing_done = on_typing_done
        self._bright = bright

        if bright:
            self._bg_color = self._color("bright_bg")
            self._text_color = self._color("bright_text")
        else:
            self._bg_color = self._color("bg")
            self._text_color = self._color("text")
        self._shadow_color = self._color("shadow")

        self._update_size()
        self.show()
        self.raise_()

        # 淡入（paintEvent 内 alpha 系数动画）
        self._start_fade_in()

        # P3：短文本更快出字（28ms/字符），长文本 8ms/字符，交互反馈不拖沓
        length = len(text)
        if length <= 8:
            speed = 28
        elif length >= 80:
            speed = 8
        else:
            speed = 28 - (length - 8) * (28 - 8) / (80 - 8)
        self._typewriter_speed = int(speed)

        if length > 0:
            self._is_typing = True
            self._typewriter_revealed = 1
            self._typewriter_timer.start(speed)
        else:
            self._typewriter_revealed = 0

        self.update()

    def append_text(self, chunk: str):
        """P2: 流式追加文字（边生成边显示）。
        
        与 set_text 不同：不重启打字机，直接在已有文本后追加。
        用于 LLM 流式输出场景。
        
        UI优化: 添加打字光标动画
        """
        if not chunk:
            return
        
        # 停止当前打字机（如果还在打字）
        self._typewriter_timer.stop()
        self._is_typing = False
        
        # 追加文本
        self._full_text += chunk
        self._text = self._full_text
        self._typewriter_revealed = len(self._text)  # 全部显示
        
        # UI优化: 显示打字光标（流式生成中）
        self._show_typing_cursor = True
        self._start_cursor_blink()
        
        # 更新大小并刷新
        self._update_size()
        self.update()
    
    def _start_cursor_blink(self):
        """UI优化: 启动打字光标闪烁动画"""
        if not hasattr(self, '_cursor_timer'):
            from PySide6.QtCore import QTimer
            self._cursor_timer = QTimer()
            self._cursor_timer.setInterval(500)
            self._cursor_timer.timeout.connect(self._cursor_tick)
        
        self._cursor_visible = True
        self._cursor_timer.start()
        
    def _cursor_tick(self):
        """UI优化: 打字光标闪烁"""
        self._cursor_visible = not self._cursor_visible
        self.update()
        
    def _stop_cursor_blink(self):
        """UI优化: 停止打字光标闪烁"""
        if hasattr(self, '_cursor_timer'):
            self._cursor_timer.stop()
        self._show_typing_cursor = False
        self._cursor_visible = False

    def set_sticker(self, emoji: str, caption: str = ""):
        """大表情贴图模式：居中玻璃卡 + 可选文案（如摸头大反应的 💕）"""
        self._typewriter_timer.stop()
        self._is_typing = False
        self._sticker_mode = True
        self._sticker_image = None
        self._sticker_emoji = emoji or "💕"
        self._sticker_caption = caption or ""
        self._full_text = caption or ""
        self._bright = False
        self._bg_color = self._color("bg")
        self._text_color = self._color("text")
        self._shadow_color = self._color("shadow")

        self._update_size()
        self.show()
        self.raise_()
        self._start_fade_in()
        self.update()

    def set_sticker_image(self, image_path: str, caption: str = ""):
        """图片贴图模式：用生成/收藏的插画作为气泡大贴图"""
        self._typewriter_timer.stop()
        self._is_typing = False
        self._sticker_mode = True
        self._sticker_emoji = ""
        pix = QPixmap(image_path)
        self._sticker_image = pix if not pix.isNull() else None
        self._sticker_caption = caption or ""
        self._full_text = caption or ""
        self._bright = False
        self._bg_color = self._color("bg")
        self._text_color = self._color("text")
        self._shadow_color = self._color("shadow")

        self._update_size()
        self.show()
        self.raise_()
        self._start_fade_in()
        self.update()

    def _start_fade_in(self):
        """淡入起点：alpha 从 0.0 起，逐步插值到 1.0（每 16ms 一步，共 176ms）。"""
        self._fade_alpha = 0.0
        self._fade_steps = 0
        self._fade_timer.stop()
        self._fade_timer.start()
        self.update()

    def _fade_tick(self):
        """paintEvent 淡入步进（子 widget 有效）。"""
        self._fade_steps += 1
        if self._fade_steps >= self._fade_total:
            self._fade_timer.stop()
            self._fade_alpha = 1.0
        else:
            self._fade_alpha = self._fade_steps / self._fade_total
        self.update()

    def _typewriter_tick(self):
        """打字机进度推进一步"""
        if self._typewriter_revealed < len(self._full_text):
            self._typewriter_revealed += 1
            self.update()

        if self._typewriter_revealed >= len(self._full_text):
            self._typewriter_timer.stop()
            self._is_typing = False
            if self._on_typing_done:
                self._on_typing_done()
                self._on_typing_done = None
            return

        ch = self._full_text[self._typewriter_revealed - 1]
        if ch in "。！？；：":
            self._typewriter_timer.start(int(self._typewriter_speed * 2.5))
        else:
            self._typewriter_timer.start(self._typewriter_speed)

    def set_typewriter_speed(self, ms_per_char: int):
        self._typewriter_speed = max(5, ms_per_char)

    def is_typing(self) -> bool:
        return self._is_typing

    def skip_typing(self):
        self._typewriter_timer.stop()
        self._typewriter_revealed = len(self._full_text)
        self._is_typing = False
        if self._on_typing_done:
            cb = self._on_typing_done
            self._on_typing_done = None
            cb()
        self.update()

    def hide_bubble(self):
        self._typewriter_timer.stop()
        self._fade_timer.stop()
        self._fade_alpha = 1.0
        self._is_typing = False
        self._sticker_mode = False
        self._sticker_image = None
        
        # UI优化: 停止流式打字光标
        self._stop_cursor_blink()
        
        self.hide()

    def _start_flash(self):
        self._flash_count = 0
        self._flash_timer.start(300)

    def _flash_tick(self):
        self._flash_count += 1
        if self._flash_count > 12:
            self._flash_timer.stop()
            self._flash_count = 0
        self.update()

    # ── 布局 / 测量 ──

    def _emoji_font(self, scale: float = 1.5) -> QFont:
        f = QFont(self._font)
        f.setPointSize(int(self._font.pointSize() * scale))
        return f

    def _tokenize(self, text: str):
        """把文本切成 (片段, 是否表情) 序列，ZWJ/FE0F 与相邻表情合并"""
        tokens = []
        buf = ""
        buf_emoji = None
        for ch in text:
            e = _is_emoji(ch)
            if buf_emoji is None:
                buf, buf_emoji = ch, e
            elif e == buf_emoji:
                buf += ch
            else:
                tokens.append((buf, buf_emoji))
                buf, buf_emoji = ch, e
        if buf:
            tokens.append((buf, buf_emoji))
        return tokens

    def _layout(self, text: str, max_w: int):
        """计算富文本分行（emoji 1.5x 尺寸，按词换行，中文按字符换行）"""
        base = self._font
        emo_font = self._emoji_font(1.5)
        base_m = QFontMetrics(base)
        emo_m = QFontMetrics(emo_font)

        lines = []
        cur = []
        cur_w = 0
        cur_h = 0
        for tok, is_emoji in self._tokenize(text):
            font = emo_font if is_emoji else base
            m = emo_m if is_emoji else base_m
            if not is_emoji:
                words = tok.split(" ")
                for wi, word in enumerate(words):
                    # 中文无空格，单字可能仍超宽，按字符进一步拆分
                    chars = list(word)
                    buf = ""
                    for ch in chars:
                        test_w = m.horizontalAdvance(buf + ch)
                        if buf and test_w > max_w:
                            # 当前行已满，换行
                            if cur_w > 0:
                                lines.append((cur, cur_w, cur_h))
                                cur, cur_w, cur_h = [], 0, 0
                            w_w = m.horizontalAdvance(buf)
                            cur.append({"text": buf, "font": font, "w": w_w, "h": m.height()})
                            cur_w += w_w
                            cur_h = max(cur_h, m.height())
                            buf = ch
                        else:
                            buf += ch
                    if buf:
                        w_w = m.horizontalAdvance(buf)
                        if cur_w > 0 and cur_w + w_w > max_w:
                            lines.append((cur, cur_w, cur_h))
                            cur, cur_w, cur_h = [], 0, 0
                        cur.append({"text": buf, "font": font, "w": w_w, "h": m.height()})
                        cur_w += w_w
                        cur_h = max(cur_h, m.height())
                    if wi < len(words) - 1:
                        sp_w = m.horizontalAdvance(" ")
                        cur.append({"text": " ", "font": font, "w": sp_w, "h": m.height()})
                        cur_w += sp_w
                        cur_h = max(cur_h, m.height())
            else:
                w_e = m.horizontalAdvance(tok)
                if cur_w > 0 and cur_w + w_e > max_w:
                    lines.append((cur, cur_w, cur_h))
                    cur, cur_w, cur_h = [], 0, 0
                cur.append({"text": tok, "font": font, "w": w_e, "h": m.height()})
                cur_w += w_e
                cur_h = max(cur_h, m.height())
        if cur:
            lines.append((cur, cur_w, cur_h))
        return lines

    def _update_size(self):
        if self._sticker_mode:
            # 图片贴图：固定 160x160 画区 + 文案
            if self._sticker_image and not self._sticker_image.isNull():
                cw = ch = 0
                if self._sticker_caption:
                    cm = QFontMetrics(self._font)
                    cw = cm.horizontalAdvance(self._sticker_caption)
                    ch = cm.height()
                bw = max(160, cw) + self._padding_h * 2 + 16
                bh = 160 + (ch + 6 if ch else 0) + self._padding_v * 2 + 8
                self.setFixedSize(max(bw, 60), max(bh, 50))
                return
            ef = QFont("Segoe UI Emoji", 40)
            em = QFontMetrics(ef)
            ew = em.horizontalAdvance(self._sticker_emoji) if self._sticker_emoji else 44
            eh = em.height()
            cw = ch = 0
            if self._sticker_caption:
                cm = QFontMetrics(self._font)
                cw = cm.horizontalAdvance(self._sticker_caption)
                ch = cm.height()
            bw = max(ew, cw) + self._padding_h * 2 + 16
            bh = eh + (ch + 6 if ch else 0) + self._padding_v * 2 + 8
            self.setFixedSize(max(bw, 60), max(bh, 50))
            return

        max_w = self._max_width - self._padding_h * 2 - 4
        self._rich_lines = self._layout(self._text, max_w)
        max_w_line = max((lw for (_, lw, _) in self._rich_lines), default=0)
        total_h = sum(lh for (_, _, lh) in self._rich_lines)
        bw = max_w_line + self._padding_h * 2 + 16
        bh = total_h + self._padding_v * 2 + 8
        self.setFixedSize(max(bw, 50), max(bh, 36))

    # ── 绘制 ──

    def paintEvent(self, event):
        if not self._full_text and not self._sticker_mode:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        r = 14
        tri_h = 8

        flashing = self._flash_timer.isActive()
        is_on = (self._flash_count % 2 == 0) if flashing else True

        bg = self._bg_color if is_on else QColor(*THEME_COLORS[self._theme]["bg"][:3], 160)
        tc = self._text_color if is_on else QColor(150, 150, 160)
        # 诊断日志：首次绘制时输出颜色信息
        if not getattr(self, "_diag_logged", False):
            logger.info(f"Bubble paint: theme={self._theme} bg=({bg.red()},{bg.green()},{bg.blue()},{int(bg.alphaF()*255)}) tc=({tc.red()},{tc.green()},{tc.blue()},{int(tc.alphaF()*255)}) fade_alpha={self._fade_alpha:.2f} text='{self._full_text[:30]}'")
            self._diag_logged = True
        # 淡入：把 _fade_alpha 应用到 bg / tc 的 alpha 通道（子 widget 生效）
        if self._fade_alpha < 1.0:
            bg.setAlphaF(bg.alphaF() * self._fade_alpha)
            tc.setAlphaF(tc.alphaF() * self._fade_alpha)

        if self._sticker_mode:
            self._paint_sticker(p, w, h, r, bg, tc)
            p.end()
            return

        body_h = h - tri_h

        # 阴影层
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(2, 2, w - 12, body_h + 2, r, r)
        p.fillPath(shadow_path, self._shadow_color)

        # 气泡主体 + 三角
        bubble_path = QPainterPath()
        bubble_path.addRoundedRect(0, 0, w - 12, body_h, r, r)
        cx = (w - 12) // 2
        bubble_path.moveTo(cx - 7, body_h)
        bubble_path.lineTo(cx, body_h + tri_h)
        bubble_path.lineTo(cx + 7, body_h)
        bubble_path.closeSubpath()
        p.fillPath(bubble_path, bg)

        # 富文本（打字机逐字）
        reveal = self._typewriter_revealed if self._is_typing and self._typewriter_revealed > 0 else len(self._full_text)
        p.setPen(tc)
        offset = 0
        cur_x = self._padding_h
        cur_y = self._padding_v
        cur_h = 0
        for line_idx, (segs, _, line_h) in enumerate(self._rich_lines):
            x = self._padding_h
            y = self._padding_v + sum(lh for (_, _, lh) in self._rich_lines[:line_idx])
            for seg in segs:
                L = len(seg["text"])
                if offset >= reveal:
                    break
                vis = seg["text"] if (reveal - offset) >= L else seg["text"][: reveal - offset]
                p.setFont(seg["font"])
                p.drawText(x, y, seg["w"], line_h, Qt.AlignLeft | Qt.AlignVCenter, vis)
                x += seg["w"]
                cur_x, cur_y, cur_h = x, y, line_h
                offset += L
            if offset >= reveal:
                break

        # 打字机光标
        if self._is_typing and reveal < len(self._full_text):
            p.setFont(self._font)
            p.drawText(cur_x, cur_y, cur_h, cur_h, Qt.AlignLeft | Qt.AlignVCenter, "▎")
        
        # UI优化: 流式打字光标（闪烁）
        if getattr(self, '_show_typing_cursor', False) and getattr(self, '_cursor_visible', False):
            p.setPen(tc)
            p.setFont(self._font)
            # 在文本末尾绘制光标
            cursor_x = cur_x + 2
            cursor_y = cur_y
            cursor_h = cur_h
            p.drawRect(cursor_x, cursor_y + 2, 2, cursor_h - 4)

        p.end()

    def _paint_sticker(self, p, w, h, r, bg, tc):
        """大表情贴图：居中玻璃卡 + 可选文案"""
        # 阴影
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(2, 2, w - 8, h - 4, r, r)
        p.fillPath(shadow_path, self._shadow_color)

        # 卡片
        card = QPainterPath()
        card.addRoundedRect(0, 0, w - 8, h - 4, r, r)
        p.fillPath(card, bg)

        cx = (w - 8) // 2
        ey = self._padding_v

        # 图片贴图
        if self._sticker_image and not self._sticker_image.isNull():
            img = self._sticker_image.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ix = (w - 8 - img.width()) // 2
            iy = ey
            p.drawPixmap(ix, iy, img)
            used_h = 160
        else:
            # 大 emoji
            ef = QFont("Segoe UI Emoji", 40)
            em = QFontMetrics(ef)
            e_size = int(em.height() * 0.92)
            p.setFont(ef)
            p.setPen(tc)
            p.drawText(cx - e_size // 2, ey, e_size, e_size, Qt.AlignCenter, self._sticker_emoji)
            used_h = e_size

        # 文案
        if self._sticker_caption:
            p.setFont(self._font)
            p.setPen(tc)
            cm = QFontMetrics(self._font)
            cw = cm.horizontalAdvance(self._sticker_caption)
            p.drawText(cx - cw // 2, ey + used_h + 6, cw, cm.height(), Qt.AlignCenter, self._sticker_caption)
