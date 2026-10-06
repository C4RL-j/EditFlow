from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


ASSET_ICON_SIZE = QSize(18, 18)
ASSET_PREVIEW_ICON_SIZE = QSize(72, 72)
EDITFLOW_ICON_COLOR = "#25D7F2"
EDITFLOW_DANGER_COLOR = "#E34B5B"


def editflow_icon(name: str, *, filled: bool = False, color: str = EDITFLOW_ICON_COLOR, size: QSize | None = None) -> QIcon:
    icon_size = size or ASSET_ICON_SIZE
    return QIcon(_icon_pixmap(name, filled, color, icon_size.width(), icon_size.height()))


def editflow_pixmap(name: str, *, filled: bool = False, color: str = EDITFLOW_ICON_COLOR, size: QSize | None = None) -> QPixmap:
    icon_size = size or ASSET_ICON_SIZE
    return _icon_pixmap(name, filled, color, icon_size.width(), icon_size.height())


@lru_cache(maxsize=128)
def _icon_pixmap(name: str, filled: bool, color: str, width: int, height: int) -> QPixmap:
    svg = _svg_markup(name, filled, color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    if not pixmap.isNull():
        return pixmap

    fallback = QPixmap(width, height)
    fallback.fill(Qt.GlobalColor.transparent)
    painter = QPainter(fallback)
    painter.setPen(QColor(color))
    painter.drawRect(2, 2, max(1, width - 4), max(1, height - 4))
    painter.end()
    return fallback


def _svg_markup(name: str, filled: bool, color: str) -> str:
    common = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">'
    )
    if name == "star":
        points = "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
        fill = color if filled else "none"
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><polygon points="{points}" fill="{fill}" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    if name == "folder":
        return common + '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H9l2 2.5h7.5A2.5 2.5 0 0 1 21 9v8.5A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M3 9h18"/></svg>'
    if name == "library":
        return common + '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H10l2 2.5h5.5A2.5 2.5 0 0 1 20 9v7.5A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z"/><path d="M4 9h16"/><path d="M7 13h10"/><path d="M7 16h6"/></svg>'
    if name == "audio":
        return common + '<path d="M7 18V6"/><path d="M7 6h10v8"/><circle cx="5.5" cy="18" r="2.5"/><circle cx="15.5" cy="14" r="2.5"/><path d="M20.5 8.5v7"/><path d="M3.5 10v4"/><path d="M22.5 10.5v3"/></svg>'
    if name == "preview":
        return common + '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z"/><circle cx="12" cy="12" r="2.5"/></svg>'
    if name == "rename":
        return common + '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M13.5 7.5l3 3"/></svg>'
    if name == "move":
        return common + '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/><path d="M5 6v12"/></svg>'
    if name == "tag":
        return common + '<path d="M20 13.5 13.5 20 4 10.5V4h6.5L20 13.5z"/><circle cx="8" cy="8" r="1"/></svg>'
    if name == "trash":
        return common + '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 14h10l1-14"/><path d="M9 7V4h6v3"/></svg>'
    if name == "speaker":
        return common + '<path d="M4 10v4h4l5 4V6l-5 4H4z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/></svg>'
    if name == "speaker-muted":
        return common + '<path d="M4 10v4h4l5 4V6l-5 4H4z"/><path d="M16 9l5 5"/><path d="M21 9l-5 5"/></svg>'
    if name == "chevron-left":
        return common + '<path d="M15 18l-6-6 6-6"/></svg>'
    if name == "chevron-right":
        return common + '<path d="M9 18l6-6-6-6"/></svg>'
    return common + '<path d="M5 5h14v14H5z"/></svg>'
