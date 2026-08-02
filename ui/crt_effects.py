"""CRT 视觉特效工具：磷光辉光（bloom）/ 玻璃质感。

独立于具体 widget，供 crt_window / amadeus_hud / ink_subtitle / crt_overlay 复用。
核心思路：
  - 磷光辉光用「离屏渲染 + 放大叠加（CompositionMode_Plus）」模拟 CRT 荧光外溢，
    比单纯调高 alpha 更像真实显像管发光。
  - 玻璃质感 = 半透明深底 + 顶部内高光 + 细发光描边。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QImage, QPen, QPainterPath


def _tint_image(src: QImage, color: QColor) -> QImage:
    """把 src 的 alpha 形状用 color 重新着色（保持透明度分布）。"""
    out = QImage(src.size(), QImage.Format_ARGB32)
    out.fill(0)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    p.drawImage(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out


def phosphor_glow_text(
    p: QPainter,
    text: str,
    x: int,
    y: int,
    font: QFont,
    color: QColor,
    glow: QColor,
    align: int = Qt.AlignLeft,
    glow_passes: int = 4,
    glow_spread: float = 0.6,
) -> None:
    """带磷光外溢的文字。

    (x, y) 为文字左上锚点；align 控制框内对齐。先在离屏渲染实色文字，
    再把着色后的副本放大、低 alpha、Plus 叠加若干层形成光晕，最后叠清晰实字。
    """
    from PySide6.QtGui import QFontMetrics

    fm = QFontMetrics(font)
    tw, th = fm.horizontalAdvance(text), fm.height()
    pad = int(max(tw, th) * glow_spread) + 6
    iw, ih = max(1, tw + pad * 2), max(1, th + pad * 2)

    img = QImage(iw, ih, QImage.Format_ARGB32)
    img.fill(0)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.Antialiasing)
    ip.setFont(font)
    ip.setPen(color)
    ip.drawText(img.rect(), Qt.AlignCenter, text)
    ip.end()

    glow_img = _tint_image(img, glow)

    p.setCompositionMode(QPainter.CompositionMode_Plus)
    cx, cy = x + tw // 2, y + th // 2
    for i in range(glow_passes, 0, -1):
        s = 1.0 + (i / glow_passes) * glow_spread
        w, h = int(iw * s), int(ih * s)
        p.setOpacity(0.55 / glow_passes * (glow_passes - i + 1))
        p.drawImage(QRect(int(cx - w // 2), int(cy - h // 2), w, h), glow_img)
    p.setOpacity(1.0)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    p.setFont(font)
    p.setPen(color)
    p.drawText(QRect(x, y, tw, th), align | Qt.AlignTop, text)


def phosphor_glow_ellipse(
    p: QPainter,
    cx: int,
    cy: int,
    r: int,
    color: QColor,
    glow: QColor,
    passes: int = 4,
    spread: float = 2.4,
) -> None:
    """带磷光外溢的圆形（电源 LED / HUD 状态点）。"""
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    for i in range(passes, 0, -1):
        rr = r * (1 + (i / passes) * spread)
        p.setOpacity(0.5 / passes * (passes - i + 1))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx, cy), int(rr), int(rr))
    p.setOpacity(1.0)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, cy), r, r)


def glass_panel(
    p: QPainter,
    rect: QRect,
    radius: int,
    bg: QColor,
    border: QColor,
    border_glow: QColor | None = None,
    top_highlight: bool = True,
) -> None:
    """玻璃质感面板：半透明深底 + 细发光描边 + 顶部内高光。"""
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    # 发光描边（可选，先画一层外溢）
    if border_glow is not None:
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(0.5)
        pg = QPen(border_glow)
        pg.setWidth(2)
        p.setPen(pg)
        p.drawPath(path)
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawPath(path)

    # 顶部内高光（玻璃反光）
    if top_highlight:
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)
        hl = QRect(rect.x(), rect.y(), rect.width(), max(2, radius // 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 36))
        p.drawRect(hl)
        p.setClipping(False)  # 复位裁剪

    # 细描边
    p.setPen(QPen(border, 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
