"""Spoti Player — custom Qt widgets (hand painted, Spotify styled)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QSizePolicy, QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from . import theme
from .library import format_time


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def pil_to_pixmap(image) -> QPixmap:
    """Convert a PIL image to a QPixmap without depending on image plugins."""
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def heart_path(rect: QRectF) -> QPainterPath:
    """A heart centred in *rect* (used for Liked Songs everywhere)."""
    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) * 0.32
    path = QPainterPath()
    path.moveTo(cx, cy + r * 1.55)
    path.cubicTo(cx - r * 2.6, cy - r * 0.45, cx - r * 0.95, cy - r * 2.1, cx, cy - r * 0.5)
    path.cubicTo(cx + r * 0.95, cy - r * 2.1, cx + r * 2.6, cy - r * 0.45, cx, cy + r * 1.55)
    return path


def draw_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor, stroke: float = 0.0) -> None:
    """Draw a vector icon centred in *rect* (no icon font required)."""
    size = min(rect.width(), rect.height())
    cx, cy = rect.center().x(), rect.center().y()
    half = size / 2.0
    width = stroke or max(1.6, size * 0.11)
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if kind == "play":
        path = QPainterPath()
        path.moveTo(cx - half * 0.55, cy - half * 0.92)
        path.lineTo(cx + half * 0.88, cy)
        path.lineTo(cx - half * 0.55, cy + half * 0.92)
        path.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
    elif kind == "pause":
        bar_w = size * 0.2
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(QRectF(cx - half * 0.85, cy - half * 0.9, bar_w, size * 0.9), 2, 2)
        painter.drawRoundedRect(QRectF(cx + half * 0.85 - bar_w, cy - half * 0.9, bar_w, size * 0.9), 2, 2)
    elif kind == "stop":
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(QRectF(cx - half * 0.72, cy - half * 0.72, size * 0.72, size * 0.72), 3, 3)
    elif kind in ("next", "prev"):
        sign = 1 if kind == "next" else -1
        path = QPainterPath()
        path.moveTo(cx - half * 0.85 * sign, cy - half * 0.8)
        path.lineTo(cx + half * 0.3 * sign, cy)
        path.lineTo(cx - half * 0.85 * sign, cy + half * 0.8)
        path.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
        bar_x = cx + half * 0.58 * sign
        painter.drawRoundedRect(QRectF(bar_x - half * 0.14, cy - half * 0.8, half * 0.28, size * 0.8), 2, 2)
    elif kind == "shuffle":
        painter.drawLine(QPointF(cx - half * 0.9, cy - half * 0.55), QPointF(cx + half * 0.3, cy + half * 0.55))
        painter.drawLine(QPointF(cx - half * 0.9, cy + half * 0.55), QPointF(cx + half * 0.3, cy - half * 0.55))
        for dy in (-1, 1):
            arrow = QPainterPath()
            arrow.moveTo(cx + half * 0.15, cy + dy * half * 0.2)
            arrow.lineTo(cx + half * 0.95, cy + dy * half * 0.75)
            arrow.lineTo(cx + half * 0.2, cy + dy * half * 1.0)
            arrow.closeSubpath()
            painter.setBrush(QBrush(color))
            painter.drawPath(arrow)
    elif kind in ("repeat", "repeat1"):
        painter.drawArc(QRectF(cx - half * 0.9, cy - half * 0.9, size * 0.9, size * 0.9), 40 * 16, 280 * 16)
        arrow = QPainterPath()
        arrow.moveTo(cx + half * 0.5, cy - half * 1.0)
        arrow.lineTo(cx + half * 1.1, cy - half * 0.3)
        arrow.lineTo(cx + half * 0.1, cy - half * 0.4)
        arrow.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(arrow)
        if kind == "repeat1":
            painter.drawText(QRectF(cx - half * 0.5, cy - half * 0.55, size * 0.5, size * 0.5),
                             Qt.AlignCenter, "1")
    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(cx - half * 0.95, cy - half * 0.5)
        path.lineTo(cx - half * 0.15, cy - half * 0.5)
        path.lineTo(cx + half * 0.05, cy - half * 0.2)
        path.lineTo(cx + half * 0.95, cy - half * 0.2)
        path.lineTo(cx + half * 0.95, cy + half * 0.7)
        path.lineTo(cx - half * 0.95, cy + half * 0.7)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind in ("up", "down"):
        sign = -1 if kind == "up" else 1
        path = QPainterPath()
        path.moveTo(cx - half * 0.8, cy + half * 0.45 * sign)
        path.lineTo(cx + half * 0.8, cy + half * 0.45 * sign)
        path.lineTo(cx, cy - half * 0.7 * sign)
        path.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
    elif kind in ("clear", "close"):
        painter.drawLine(QPointF(cx - half * 0.68, cy - half * 0.68), QPointF(cx + half * 0.68, cy + half * 0.68))
        painter.drawLine(QPointF(cx + half * 0.68, cy - half * 0.68), QPointF(cx - half * 0.68, cy + half * 0.68))
    elif kind == "refresh":
        painter.drawArc(QRectF(cx - half * 0.85, cy - half * 0.85, size * 0.85, size * 0.85), 30 * 16, 280 * 16)
        arrow = QPainterPath()
        arrow.moveTo(cx + half * 0.3, cy - half * 1.0)
        arrow.lineTo(cx + half * 1.0, cy - half * 0.25)
        arrow.lineTo(cx + half * 0.02, cy - half * 0.35)
        arrow.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(arrow)
    elif kind in ("volume", "mute"):
        path = QPainterPath()
        path.moveTo(cx - half * 0.9, cy - half * 0.28)
        path.lineTo(cx - half * 0.35, cy - half * 0.28)
        path.lineTo(cx + half * 0.12, cy - half * 0.8)
        path.lineTo(cx + half * 0.12, cy + half * 0.8)
        path.lineTo(cx - half * 0.35, cy + half * 0.28)
        path.lineTo(cx - half * 0.9, cy + half * 0.28)
        path.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
        if kind == "mute":
            painter.drawLine(QPointF(cx + half * 0.42, cy - half * 0.32), QPointF(cx + half * 1.0, cy + half * 0.32))
            painter.drawLine(QPointF(cx + half * 1.0, cy - half * 0.32), QPointF(cx + half * 0.42, cy + half * 0.32))
        else:
            painter.drawArc(QRectF(cx + half * 0.05, cy - half * 0.55, size * 0.8, size * 0.55), -60 * 16, 120 * 16)
    elif kind == "search":
        painter.drawEllipse(QRectF(cx - half * 0.85, cy - half * 0.85, size * 0.62, size * 0.62))
        painter.drawLine(QPointF(cx + half * 0.15, cy + half * 0.15), QPointF(cx + half * 0.85, cy + half * 0.85))
    elif kind == "eq":
        for i, frac in enumerate((0.45, 0.95, 0.65)):
            x = cx + (i - 1) * half * 0.6
            painter.drawLine(QPointF(x, cy + half * 0.75), QPointF(x, cy + half * 0.75 - frac * half * 1.5))
    elif kind == "discord":
        # stylised controller-ish blob in the Discord blurple
        path = QPainterPath()
        path.addRoundedRect(QRectF(cx - half * 0.9, cy - half * 0.62, size * 0.9, size * 0.62), size * 0.3, size * 0.3)
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
        painter.setPen(QPen(theme.color(theme.BG1), max(1.0, size * 0.08)))
        painter.drawEllipse(QRectF(cx - half * 0.45, cy - half * 0.28, size * 0.18, size * 0.18))
        painter.drawEllipse(QRectF(cx + half * 0.13, cy - half * 0.28, size * 0.18, size * 0.18))
    elif kind == "power":
        painter.drawArc(QRectF(cx - half * 0.82, cy - half * 0.75, size * 0.82, size * 0.82),
                        -55 * 16, 290 * 16)
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(cx, cy - half * 1.0), QPointF(cx, cy - half * 0.05))
    elif kind == "bolt":
        # lightning bolt for performance mode
        bolt = QPainterPath()
        bolt.moveTo(cx + half * 0.25, cy - half * 0.95)
        bolt.lineTo(cx - half * 0.55, cy + half * 0.1)
        bolt.lineTo(cx - half * 0.02, cy + half * 0.1)
        bolt.lineTo(cx - half * 0.25, cy + half * 0.95)
        bolt.lineTo(cx + half * 0.6, cy - half * 0.15)
        bolt.lineTo(cx + half * 0.05, cy - half * 0.15)
        bolt.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(bolt)
    elif kind == "download":
        # arrow into a tray (save offline)
        painter.drawLine(QPointF(cx, cy - half * 0.9), QPointF(cx, cy + half * 0.25))
        arrow = QPainterPath()
        arrow.moveTo(cx - half * 0.5, cy - half * 0.15)
        arrow.lineTo(cx, cy + half * 0.38)
        arrow.lineTo(cx + half * 0.5, cy - half * 0.15)
        arrow.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(arrow)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx - half * 0.85, cy + half * 0.78),
                         QPointF(cx + half * 0.85, cy + half * 0.78))
    elif kind == "check":
        painter.drawLine(QPointF(cx - half * 0.75, cy), QPointF(cx - half * 0.1, cy + half * 0.6))
        painter.drawLine(QPointF(cx - half * 0.1, cy + half * 0.6), QPointF(cx + half * 0.85, cy - half * 0.55))
    elif kind == "pin":
        # "keep on top": a bar with an arrow pushing up into it
        painter.drawLine(QPointF(cx - half * 0.9, cy + half * 0.75), QPointF(cx + half * 0.9, cy + half * 0.75))
        arrow = QPainterPath()
        arrow.moveTo(cx - half * 0.55, cy + half * 0.3)
        arrow.lineTo(cx + half * 0.55, cy + half * 0.3)
        arrow.lineTo(cx, cy - half * 0.75)
        arrow.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(arrow)
    elif kind == "mini":
        bar = QRectF(cx - half * 0.95, cy - half * 0.62, size * 0.95, size * 0.62)
        painter.drawRoundedRect(bar, 3, 3)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(bar.left() + size * 0.1, bar.center().y() - size * 0.1,
                                   size * 0.2, size * 0.2))
        painter.setPen(QPen(color, max(1.2, size * 0.09), Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(bar.left() + size * 0.4, bar.center().y() - size * 0.08),
                         QPointF(bar.right() - size * 0.08, bar.center().y() - size * 0.08))
        painter.drawLine(QPointF(bar.left() + size * 0.4, bar.center().y() + size * 0.09),
                         QPointF(bar.right() - size * 0.2, bar.center().y() + size * 0.09))
    elif kind == "back":
        painter.drawLine(QPointF(cx + half * 0.28, cy - half * 0.62),
                         QPointF(cx - half * 0.32, cy))
        painter.drawLine(QPointF(cx - half * 0.32, cy),
                         QPointF(cx + half * 0.28, cy + half * 0.62))
    elif kind == "forward":
        painter.drawLine(QPointF(cx - half * 0.28, cy - half * 0.62),
                         QPointF(cx + half * 0.32, cy))
        painter.drawLine(QPointF(cx + half * 0.32, cy),
                         QPointF(cx - half * 0.28, cy + half * 0.62))
    elif kind == "home":
        roof = QPainterPath()
        roof.moveTo(cx - half * 0.95, cy + half * 0.05)
        roof.lineTo(cx, cy - half * 0.85)
        roof.lineTo(cx + half * 0.95, cy + half * 0.05)
        roof.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(roof)
        painter.drawRoundedRect(QRectF(cx - half * 0.58, cy + half * 0.02,
                                       size * 0.58, size * 0.7), 2, 2)
    elif kind in ("heart", "heart_filled"):
        heart = heart_path(QRectF(cx - size / 2, cy - size / 2, size, size))
        if kind == "heart_filled":
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
        painter.drawPath(heart)
    elif kind == "radio":
        # broadcast tower: mast with side arcs and radio waves
        painter.drawRoundedRect(QRectF(cx - half * 0.14, cy - half * 0.1,
                                       size * 0.28, size * 0.95), 2, 2)
        painter.drawEllipse(QRectF(cx - half * 0.34, cy - half * 0.86,
                                   size * 0.68, size * 0.42))
        painter.drawArc(QRectF(cx - half * 0.85, cy - half * 0.95,
                               size * 0.85, size * 0.85), -50 * 16, 100 * 16)
        painter.drawArc(QRectF(cx, cy - half * 0.95, size * 0.85, size * 0.85),
                        30 * 16, 100 * 16)
    elif kind == "cog":
        painter.drawEllipse(QRectF(cx - half * 0.42, cy - half * 0.42, size * 0.42, size * 0.42))
        for i in range(8):
            angle = math.radians(i * 45)
            x1 = cx + math.cos(angle) * half * 0.5
            y1 = cy + math.sin(angle) * half * 0.5
            x2 = cx + math.cos(angle) * half * 0.9
            y2 = cy + math.sin(angle) * half * 0.9
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


# --------------------------------------------------------------------------- #
#  buttons
# --------------------------------------------------------------------------- #
class IconButton(QAbstractButton):
    """Round icon button.

    Spotify behaviour: idle icons are white-ish, hovering shows a soft grey
    circle (the icon stays white) and the main play/pause button is the big
    white circle with a black glyph. A *checked* button (shuffle on, repeat
    on, mini bar on) turns its icon Spotify green. Buttons are momentary
    unless ``setCheckable(True)`` is called, so the transport arrows never
    stay lit after a click.

    ``style`` picks the circle drawn behind the glyph:

    * ``"plain"`` — nothing (or a soft hover circle) and a green checked state
    * ``"white"`` — Spotify's solid white circle with a black glyph (the play
      button, the Home button while it is active)
    * ``"green"`` — Spotify's green circle with a black glyph (the big play
      button of the playlist hero)
    * ``"dark"``  — Spotify's flat dark-grey circle with a white glyph
      (the back / forward buttons of the top bar)
    """

    def __init__(self, kind: str = "play", size: int = 38, accent: str = theme.CYAN,
                 tooltip: str = "", parent=None, style: str = "plain"):
        super().__init__(parent)
        self.kind = kind
        self.accent = theme.color(accent)
        self.style = style
        self._size = size
        self._hover = False
        # momentary by default: toggles (shuffle / repeat / mini bar) opt in
        # with setCheckable(True) — otherwise a click would latch the button
        # and it would keep lighting up like a toggle
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        if tooltip:
            self.setToolTip(tooltip)
        self.setFixedSize(size, size)

    # -- api -------------------------------------------------------------- #
    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_style(self, style: str) -> None:
        if style != self.style:
            self.style = style
            self.update()

    def set_accent(self, accent: str | QColor) -> None:
        self.accent = theme.color(accent)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    # -- events ----------------------------------------------------------- #
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def checkStateSet(self):  # pragma: no cover - Qt hook
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        checked = self.isChecked()
        pressed = self.isDown()
        is_transport = self.kind in ("play", "pause")
        accent = self.accent if checked else theme.color(theme.GREEN)
        style = self.style or ("white" if is_transport else "plain")

        if style == "white":
            # Spotify's solid white circle with a black glyph
            grow = -1.5 if (self._hover or pressed) else 0.0
            circle = rect.adjusted(grow, grow, -grow, -grow)
            painter.setBrush(QBrush(theme.color(theme.TEXT)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(circle)
            icon_color = theme.color("#000000")
        elif style == "dark":
            # Spotify's navigation circles: flat dark grey, white glyph
            fill = theme.color("#2A2A2A" if (self._hover or pressed) else "#1F1F1F")
            painter.setBrush(QBrush(fill))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(rect)
            icon_color = theme.color(theme.TEXT if self.isEnabled() else theme.TEXT_MUTED)
            is_transport = False
        elif style == "green":
            # Spotify's big hero play button: green circle, black glyph
            grow = -1.5 if (self._hover or pressed) else 0.0
            circle = rect.adjusted(grow, grow, -grow, -grow)
            painter.setBrush(QBrush(theme.color(
                theme.GREEN_LIGHT if pressed else
                (theme.lighten(theme.GREEN, 0.15) if self._hover else theme.GREEN))))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(circle)
            icon_color = theme.color("#000000")
            is_transport = False
        else:
            if checked:
                painter.setBrush(QBrush(theme.color(accent, 0.16)))
                painter.setPen(Qt.NoPen)
                painter.drawPath(rounded_path(rect, rect.height() / 2))
                icon_color = accent
            elif self._hover or pressed:
                painter.setBrush(QBrush(theme.color("#FFFFFF", 0.14 if pressed else 0.10)))
                painter.setPen(Qt.NoPen)
                painter.drawPath(rounded_path(rect, rect.height() / 2))
                icon_color = theme.color(theme.TEXT)
            else:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(Qt.NoPen)
                icon_color = theme.color(theme.TEXT_DIM)

            if checked:
                dot = QRectF(rect.center().x() - 2, rect.bottom() - 4.5, 4, 4)
                painter.setBrush(QBrush(accent))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(dot)

        if not self.isEnabled():
            icon_color = theme.color(theme.TEXT_MUTED)

        icon_box = rect.height() * (0.46 if is_transport else 0.5)
        draw_icon(painter, self.kind, QRectF(
            rect.center().x() - icon_box / 2, rect.center().y() - icon_box / 2, icon_box, icon_box
        ), icon_color)
        painter.end()


class PillButton(QPushButton):
    """Spotify pill button.

    * ``solid``   — Spotify's main call to action: green fill, black text.
    * ``outline`` — Spotify's secondary button: hairline border, white text,
      inverting to a white fill with black text on hover.
    * ``ghost``   — transparent, subdued text, lighter on hover.
    """

    def __init__(self, text: str, color: str = theme.VIOLET, variant: str = "ghost",
                 icon: str | None = None, height: int = 32, font_size: int = 9,
                 tooltip: str = "", width: int | None = None, parent=None):
        super().__init__(text, parent)
        self.accent = theme.color(color)
        self.variant = variant
        self.icon_kind = icon
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height)
        self.setFont(theme.font(font_size, 700))
        self.setAttribute(Qt.WA_Hover, True)
        if tooltip:
            self.setToolTip(tooltip)
        metrics = QFontMetrics(self.font())
        natural = metrics.horizontalAdvance(text) + (58 if icon else 38)
        self.setMinimumWidth(width or natural)
        if width:
            self.setMaximumWidth(width)

    def set_accent(self, color: str | QColor) -> None:
        self.accent = theme.color(color)
        self.update()

    def set_variant(self, variant: str) -> None:
        self.variant = variant
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self.text()) + (58 if self.icon_kind else 38)
        return QSize(width, self.height())

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        pressed = self.isDown()

        if self.variant == "solid":
            base = theme.lighten(self.accent, 0.10 if self._hover else 0.0)
            if pressed:
                base = theme.darken(self.accent, 0.12)
            fill = base
            text_color = theme.color("#000000")
            border = Qt.NoPen
        elif self.variant == "outline":
            if self._hover or pressed:
                fill = theme.color(theme.TEXT if not pressed else theme.TEXT_DIM)
                text_color = theme.color("#000000")
                border = Qt.NoPen
            else:
                fill = theme.color("#000000", 0.0)
                text_color = theme.color(theme.TEXT)
                border = QPen(theme.color(theme.TEXT_MUTED), 1.0)
        else:  # ghost
            fill = theme.color("#FFFFFF", 0.12 if pressed else (0.08 if self._hover else 0.0))
            text_color = theme.color(theme.TEXT if self._hover else theme.TEXT_DIM)
            border = Qt.NoPen

        if not self.isEnabled():
            fill = theme.color(theme.BG3, 0.7)
            text_color = theme.color(theme.TEXT_MUTED)
            border = QPen(theme.color(theme.STROKE), 1.0)

        painter.setBrush(QBrush(fill))
        painter.setPen(border if isinstance(border, QPen) else Qt.NoPen)
        painter.drawPath(rounded_path(rect, radius))

        if self.icon_kind:
            icon_box = rect.height() * 0.5
            draw_icon(painter, self.icon_kind, QRectF(
                rect.left() + 12, rect.center().y() - icon_box / 2, icon_box, icon_box
            ), text_color)
            text_rect = rect.adjusted(icon_box + 16, 0, -8, 0)
        else:
            text_rect = rect

        painter.setPen(QPen(text_color))
        painter.setFont(self.font())
        painter.drawText(text_rect, Qt.AlignCenter, self.text())
        painter.end()


class Chip(QLabel):
    """Small coloured pill with a status dot (header info)."""

    def __init__(self, text: str, color: str = theme.VIOLET, dot: bool = True, parent=None):
        super().__init__(text, parent)
        self.accent = theme.color(color)
        self.show_dot = dot
        self.setFont(theme.font(8, 700))
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(24)

    def set_chip(self, text: str | None = None, color: str | QColor | None = None) -> None:
        if text is not None:
            self.setText(text)
        if color is not None:
            self.accent = theme.color(color)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self.text()) + (30 if self.show_dot else 20)
        return QSize(width, 24)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Spotify-style neutral pill: dark grey fill, hairline, one accent dot
        painter.setBrush(QBrush(theme.color(theme.BG3, 0.9)))
        painter.setPen(QPen(theme.color(theme.STROKE), 1.0))
        painter.drawPath(rounded_path(rect, rect.height() / 2))

        text_rect = rect.adjusted(10, 0, -10, 0)
        if self.show_dot:
            dot = QRectF(rect.left() + 9, rect.center().y() - 3, 6, 6)
            painter.setBrush(QBrush(self.accent))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(dot)
            text_rect = rect.adjusted(19, 0, -8, 0)
        painter.setPen(QPen(theme.color(theme.TEXT)))
        painter.setFont(self.font())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()


# --------------------------------------------------------------------------- #
#  panels & section headers
# --------------------------------------------------------------------------- #
class DropOverlay(QWidget):
    """Translucent 'drop it here' layer shown while dragging files over the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self.message = "Drop a folder to load it — or drop songs to add them"
        self.accent = theme.color(theme.GREEN)

    def set_message(self, text: str, accent: str | QColor | None = None) -> None:
        self.message = text
        if accent is not None:
            self.accent = theme.color(accent)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), theme.color(theme.BG0, 0.55))

        rect = QRectF(self.rect()).adjusted(26, 26, -26, -26)
        pen = QPen(self.accent, 2.0, Qt.DashLine)
        pen.setDashPattern([7, 5])
        painter.setPen(pen)
        painter.setBrush(QBrush(theme.color(self.accent, 0.05)))
        painter.drawPath(rounded_path(rect, 12))

        box = QRectF(0, 0, min(560.0, rect.width() - 40), 92)
        box.moveCenter(rect.center())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.STROKE, 0.98)))
        painter.drawPath(rounded_path(box, 8))
        painter.setPen(QPen(theme.color(theme.STROKE_HI), 1.0))
        painter.drawPath(rounded_path(box, 8))

        icon_box = 34.0
        draw_icon(painter, "folder", QRectF(box.left() + 18, box.center().y() - icon_box / 2,
                                            icon_box, icon_box),
                  theme.color(self.accent))
        painter.setPen(QPen(theme.color(theme.TEXT)))
        painter.setFont(theme.font(11, 700))
        painter.drawText(box.adjusted(66, 18, -18, -18), Qt.AlignLeft | Qt.AlignVCenter,
                         self.message.splitlines()[0])
        if "\n" in self.message:
            painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
            painter.setFont(theme.font(8))
            painter.drawText(box.adjusted(66, 46, -18, -14), Qt.AlignLeft | Qt.AlignVCenter,
                             self.message.splitlines()[1])
        painter.end()


class Panel(QFrame):
    """Rounded panel with a coloured accent line across the top.

    ``base="black"`` paints Spotify's sidebar / now-playing column black (the
    #000000 of the app frame); the default keeps the #121212 surface of the
    main content column.
    """

    def __init__(self, accent: str = theme.VIOLET, parent=None, base: str = "panel"):
        super().__init__(parent)
        self.accent = theme.color(accent)
        self.base = base
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMinimumWidth(0)

    def set_accent(self, accent: str | QColor) -> None:
        self.accent = theme.color(accent)
        self.update()

    def paintEvent(self, _event):
        # Spotify does not outline its panels: every column is one flat surface
        # (black sidebar / #121212 content) on the black app frame, separated
        # only by the gap between them.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = rounded_path(rect, 8)
        # every column is the same #121212 surface; the black of the frame shows
        # through the gaps between them, exactly like Spotify's desktop layout
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, QBrush(theme.color(theme.BG1)))
        painter.end()


class SectionHeader(QWidget):
    """Coloured bar + uppercase section title, with room for right-hand widgets."""

    def __init__(self, title: str, color: str = theme.VIOLET, parent=None):
        super().__init__(parent)
        self.accent = theme.color(color)
        self.title = title
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.label = QLabel(title)
        self.label.setObjectName("SectionTitle")
        self.label.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        self.row.addWidget(self.label)
        self.row.addStretch(1)
        self.setFixedHeight(26)

    def add_right(self, widget: QWidget) -> None:
        self.row.addWidget(widget)

    def paintEvent(self, _event):
        # Spotify marks its sections with type only (white bold heading, no
        # coloured bar), so the header paints nothing of its own.
        return


class Card(QFrame):
    """Small raised card used for the volume / device / discord blocks.

    Spotify's card: flat #181818 on the panel, hairline border, 8 px radius.
    """

    def __init__(self, accent: str = theme.CYAN, parent=None):
        super().__init__(parent)
        self.accent = theme.color(accent)
        self.setObjectName("Card")

    def paintEvent(self, _event):
        # Spotify's card: a flat #181818 block, no border at all
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.BG2)))
        painter.drawPath(rounded_path(rect, 8))
        painter.end()


# --------------------------------------------------------------------------- #
#  Spotify top bar
# --------------------------------------------------------------------------- #
class SearchField(QLineEdit):
    """Spotify's search pill: dark, fully rounded, with a painted magnifier."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchPill")
        self.setClearButtonEnabled(True)
        self.setFixedHeight(36)
        palette = self.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, theme.color("#FFFFFF", 0.62))
        self.setPalette(palette)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(13, (self.height() - 15) / 2, 15, 15)
        painter.setPen(QPen(theme.color(theme.TEXT_DIM), 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(box.adjusted(0, 0, -4, -4))
        painter.drawLine(QPointF(box.right() - 4.5, box.bottom() - 4.5),
                         QPointF(box.right() + 1.0, box.bottom() + 1.0))
        painter.end()


class TopBar(QFrame):
    """Spotify's top bar — dark, with back / forward / home and the search box.

    The bar keeps Spotify's near-black header and blends its middle towards
    the dominant colour of the song that is playing, so the header tints with
    the music the way Spotify tints a playlist page — never a bright band.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent = theme.color(theme.GREEN)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(12, 8, 12, 8)
        self.row.setSpacing(8)
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- api -------------------------------------------------------------- #
    def set_accent(self, accent: str | QColor) -> None:
        tint = theme.color(accent)
        if tint != self.accent:
            self.accent = tint
            self.update()

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.row.addWidget(widget, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self.row.addStretch(stretch)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = rounded_path(rect, 8)
        # Spotify's top bar is #121212; it only leans a hair towards the colour
        # of what is playing, never into a coloured band.
        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        grad.setColorAt(0.0, theme.color(theme.BG1))
        grad.setColorAt(0.5, theme.mix(theme.BG1, self.accent, 0.04))
        grad.setColorAt(1.0, theme.color(theme.BG1))
        painter.fillPath(path, QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        painter.end()


class LikedRow(QWidget):
    """The pinned "Liked Songs" entry of the library (Spotify's sidebar row)."""

    clicked = Signal()

    TILE = 40.0
    ROW = int(TILE) + 12

    def __init__(self, parent=None, title: str = "Liked Songs", kind: str = "heart",
                 tooltip: str = "Liked Songs — every hearted song in one playlist",
                 gradient: tuple[str, str] | None = None):
        super().__init__(parent)
        self.count = 0
        self.active = False
        self._hover = False
        self.title = title
        self.kind = kind
        self.gradient = gradient or (theme.MAGENTA, theme.BLUE)
        self.setFixedHeight(self.ROW)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(tooltip)

    def set_state(self, count: int, active: bool) -> None:
        self.count = int(count)
        self.active = bool(active)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(0, self.ROW)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 1, -2, -1)
        if self.active or self._hover:
            painter.setBrush(QBrush(theme.color("#FFFFFF", 0.10 if self.active else 0.06)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(rect, 4))

        tile = QRectF(rect.left() + 6, rect.center().y() - self.TILE / 2, self.TILE, self.TILE)
        grad = QLinearGradient(tile.left(), tile.top(), tile.right(), tile.bottom())
        grad.setColorAt(0.0, theme.color(self.gradient[0]))
        grad.setColorAt(1.0, theme.color(self.gradient[1]))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(rounded_path(tile, 4))
        if self.kind == "heart":
            painter.setBrush(QBrush(theme.color(theme.TEXT)))
            painter.drawPath(heart_path(tile.adjusted(9, 9, -9, -9)))
        else:
            draw_icon(painter, self.kind, tile.adjusted(10, 10, -10, -10), theme.color(theme.TEXT))

        text_left = tile.right() + 10
        width = max(30.0, rect.right() - text_left - 8)
        painter.setFont(theme.font(9, 600))
        painter.setPen(QPen(theme.color(theme.TEXT)))
        painter.drawText(QRectF(text_left, rect.center().y() - 19, width, 18),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(self.title, Qt.ElideRight, int(width)))
        painter.setFont(theme.font(8))
        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        text = f"Playlist • {self.count} song{'' if self.count == 1 else 's'}"
        painter.drawText(QRectF(text_left, rect.center().y() + 1, width, 16),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, int(width)))
        painter.end()


# --------------------------------------------------------------------------- #
#  sliders
# --------------------------------------------------------------------------- #
class SeekBar(QSlider):
    """Track position slider: Spotify style — grey groove, white progress that
    turns green on hover, knob appears while hovering/dragging, click to jump."""

    scrubStarted = Signal()
    scrubbed = Signal(float)
    committed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 1000)
        self.setValue(0)
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)

    def set_position(self, fraction: float) -> None:
        if not self.isSliderDown():
            self.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def fraction(self) -> float:
        return self.value() / 1000.0

    def _fraction_at(self, x: float) -> float:
        margin = 9.0
        span = max(1.0, self.width() - margin * 2)
        return max(0.0, min(1.0, (x - margin) / span))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setSliderDown(True)
            self.scrubStarted.emit()
            value = self._fraction_at(event.position().x())
            self.setValue(int(value * 1000))
            self.scrubbed.emit(value)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            value = self._fraction_at(event.position().x())
            self.setValue(int(value * 1000))
            self.scrubbed.emit(value)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            self.committed.emit(self.fraction())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 9.0
        cy = self.height() / 2
        bar_h = 4.0
        live = self._hover or self.isSliderDown()
        track = QRectF(margin, cy - bar_h / 2, self.width() - margin * 2, bar_h)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.STROKE_HI)))
        painter.drawPath(rounded_path(track, bar_h / 2))

        pos = track.left() + track.width() * self.fraction()
        if pos > track.left() + 1:
            played = QRectF(track.left(), track.top(), pos - track.left(), track.height())
            color = theme.color(theme.GREEN) if live else theme.color(theme.TEXT)
            painter.setBrush(QBrush(color))
            painter.drawPath(rounded_path(played, bar_h / 2))

        if live:
            radius = 6.0
            painter.setBrush(QBrush(theme.color(theme.TEXT)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(pos, cy), radius, radius)
        painter.end()


class VolumeBar(QSlider):
    """Volume drive 0-125 %: Spotify green groove that turns coral past 100 %,
    with a marker and a knob that appears on hover."""

    valueChangedByUser = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 100)
        self.setValue(70)
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)

    def set_volume(self, volume: int, maximum: int) -> None:
        if not self.isSliderDown():
            self.setMaximum(maximum)
            self.setValue(volume)
            self.update()

    def _fraction_at(self, x: float) -> float:
        margin = 9.0
        span = max(1.0, self.width() - margin * 2)
        return max(0.0, min(1.0, (x - margin) / span))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setSliderDown(True)
            value = int(self._fraction_at(event.position().x()) * self.maximum() + 0.5)
            self.setValue(value)
            self.valueChangedByUser.emit(value)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            value = int(self._fraction_at(event.position().x()) * self.maximum() + 0.5)
            self.setValue(value)
            self.valueChangedByUser.emit(value)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            self.valueChangedByUser.emit(self.value())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 9.0
        cy = self.height() / 2
        bar_h = 7.0 if self._hover else 6.0
        track = QRectF(margin, cy - bar_h / 2, self.width() - margin * 2, bar_h)
        bar_h = 4.0
        track = QRectF(margin, cy - bar_h / 2, self.width() - margin * 2, bar_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.STROKE_HI)))
        painter.drawPath(rounded_path(track, bar_h / 2))

        maximum = max(1, self.maximum())
        pos = track.left() + track.width() * (self.value() / maximum)
        if pos > track.left() + 1:
            played = QRectF(track.left(), track.top(), pos - track.left(), track.height())
            loud = self.value() > 100
            grad = QLinearGradient(played.left(), 0, track.right(), 0)
            grad.setColorAt(0.0, theme.color(theme.TEXT))
            if loud:
                grad.setColorAt(min(0.999, 100.0 / maximum), theme.color(theme.TEXT))
                grad.setColorAt(1.0, theme.color(theme.CORAL))
            else:
                grad.setColorAt(1.0, theme.color(theme.TEXT))
            painter.setBrush(QBrush(grad))
            painter.drawPath(rounded_path(played, bar_h / 2))

        if maximum > 100:                                  # 100 % marker
            mark_x = track.left() + track.width() * (100 / maximum)
            painter.setPen(QPen(theme.color(theme.TEXT_MUTED, 0.9), 1.0))
            painter.drawLine(QPointF(mark_x, cy - 8), QPointF(mark_x, cy + 8))

        if self._hover or self.isSliderDown():
            radius = 6.0
            painter.setBrush(QBrush(theme.color("#FFFFFF")))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(pos, cy), radius, radius)
        painter.end()


# --------------------------------------------------------------------------- #
#  playlist
# --------------------------------------------------------------------------- #
ROW_HEIGHT = 56


#: Spotify's playlist columns, shared by the header and the rows
PLAYLIST_INDEX_W = 30.0
PLAYLIST_TIME_W = 52.0


class PlaylistDelegate(QStyledItemDelegate):
    def __init__(self, view: "PlaylistView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        """One Spotify track row: number, title, artist • album and duration."""
        track = index.data(Qt.UserRole)
        if track is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -4, -1)
        row = index.row()
        current = row == self.view.current_index
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        tint = self.view.tint

        if current:
            painter.setBrush(QBrush(theme.color("#FFFFFF", 0.07)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(rect, 4))
            if self.view.flash > 0.01:
                painter.setBrush(QBrush(theme.color(tint, 0.22 * self.view.flash)))
                painter.drawPath(rounded_path(rect, 4))
        elif hover or selected:
            painter.setBrush(QBrush(theme.color("#FFFFFF", 0.07 if hover else 0.05)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(rect, 4))

        # --- index column (or the little playing bars) -------------------- #
        index_rect = QRectF(rect.left(), rect.top(), PLAYLIST_INDEX_W, rect.height())
        if current and self.view.playing:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(theme.color(theme.GREEN)))
            for i in range(3):
                height = 5 + self.view.bar_heights[i] * 13
                painter.drawPath(rounded_path(
                    QRectF(index_rect.right() - 16 + i * 4.5,
                           rect.center().y() - height / 2, 2.6, height), 1.3))
        else:
            painter.setPen(QPen(theme.color(theme.GREEN) if current else theme.color(theme.TEXT_DIM)))
            painter.setFont(theme.mono(9))
            painter.drawText(index_rect.adjusted(0, 0, -8, 0),
                             Qt.AlignVCenter | Qt.AlignRight, str(row + 1))

        # --- title + subtitle ------------------------------------------- #
        text_left = rect.left() + PLAYLIST_INDEX_W + 6
        duration_text = format_time(getattr(track, "duration", 0.0))
        painter.setFont(theme.mono(8))
        duration_width = painter.fontMetrics().horizontalAdvance(duration_text) + 10
        text_width = max(40.0, rect.width() - (text_left - rect.left()) - duration_width - 10)

        painter.setFont(theme.font(10, 600))
        title = painter.fontMetrics().elidedText(track.display_title, Qt.ElideRight, int(text_width))
        painter.setPen(QPen(theme.color(theme.GREEN) if current else theme.color(theme.TEXT)))
        painter.drawText(QRectF(text_left, rect.top() + 6, text_width, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, title)

        # untagged files have no artist/album: say so instead of a bare dash
        sub_text = track.subtitle() if (track.artist or track.album) else track.display_artist
        painter.setFont(theme.font(8))
        subtitle = painter.fontMetrics().elidedText(sub_text, Qt.ElideRight, int(text_width))
        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        painter.drawText(QRectF(text_left, rect.top() + 26, text_width, 16),
                         Qt.AlignVCenter | Qt.AlignLeft, subtitle)

        painter.setFont(theme.mono(8))
        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        painter.drawText(QRectF(rect.right() - duration_width - 8, rect.top(),
                                duration_width, rect.height()),
                         Qt.AlignVCenter | Qt.AlignRight, duration_text)

        # Spotify marks the liked songs of a playlist with a little green heart
        if getattr(track, "path", "") in getattr(self.view, "liked", ()):
            heart = QRectF(rect.right() - duration_width - 30, rect.center().y() - 7, 14, 14)
            painter.setBrush(QBrush(theme.color(theme.GREEN)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(heart_path(heart))
        painter.restore()


class PlaylistHeader(QWidget):
    """Spotify's playlist column header: #, TITLE, ARTIST and the duration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left = 2.0
        clock_w = PLAYLIST_TIME_W
        text_left = left + PLAYLIST_INDEX_W + 6
        width = max(40.0, self.width() - text_left - clock_w)

        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        painter.setFont(theme.font(8, 700))
        painter.drawText(QRectF(left, 0, PLAYLIST_INDEX_W - 8, self.height()),
                         Qt.AlignVCenter | Qt.AlignRight, "#")
        painter.drawText(QRectF(text_left, 0, width * 0.5, self.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, "TITLE")
        painter.drawText(QRectF(text_left + width * 0.5, 0, width * 0.5, self.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, "ARTIST")
        # a small hand-drawn clock at the end of the row (no font glyph needed)
        cx = self.width() - 16.0
        cy = self.height() / 2.0
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color(theme.TEXT_DIM), 1.2))
        painter.drawEllipse(QPointF(cx, cy), 6.0, 6.0)
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy - 3.6))
        painter.drawLine(QPointF(cx, cy), QPointF(cx + 2.8, cy + 0.8))

        painter.setPen(QPen(theme.color(theme.STROKE), 1.0))
        painter.drawLine(QPointF(left, self.height() - 0.5),
                         QPointF(self.width() - 6, self.height() - 0.5))
        painter.end()


class PlaylistTileDelegate(QStyledItemDelegate):
    """Draws a folder as a Spotify playlist row: cover tile, name, song count.

    The item's text is kept (it carries the radio markers and the counts for
    accessibility), but the row itself is painted the Spotify way: a small
    gradient cover like a playlist crop, the name in white and a subtitle
    saying how many songs the playlist holds.
    """

    TILE = 40.0
    ROW = int(TILE) + 12          # one playlist row, used to size the list exactly

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW)

    def paint(self, painter: QPainter, option, index: "QModelIndex"):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        label = str(index.data(Qt.UserRole + 1) or "")
        count = index.data(Qt.UserRole + 2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        current = selected

        painter.setBrush(QBrush(theme.color("#FFFFFF", 0.10 if hover else 0.07)
                                if current else
                                (theme.color("#FFFFFF", 0.06) if hover else theme.color("#000000", 0.0))))
        painter.setPen(Qt.NoPen)
        painter.drawPath(rounded_path(rect, 4))

        # ---- cover tile -------------------------------------------------- #
        tile = QRectF(rect.left() + 6, rect.center().y() - self.TILE / 2, self.TILE, self.TILE)
        first, second = theme.playlist_gradient("All songs" if label == "All songs" else label)
        grad = QLinearGradient(tile.left(), tile.top(), tile.right(), tile.bottom())
        grad.setColorAt(0.0, theme.color(second))
        grad.setColorAt(1.0, theme.color(first))
        painter.setBrush(QBrush(grad))
        painter.drawPath(rounded_path(tile, 4))
        # a small mark in the corner, like Spotify's playlist tile
        painter.setPen(Qt.NoPen)
        if label == "All songs":
            # the whole folder: a white music note (the heart is Liked Songs)
            painter.setBrush(QBrush(theme.color("#FFFFFF")))
            cx, cy = tile.center().x(), tile.center().y()
            r = tile.width() * 0.13
            painter.drawEllipse(QPointF(cx - r * 0.5, cy + r * 1.5), r * 1.25, r)
            painter.drawPath(rounded_path(QRectF(cx + r * 0.45, cy - r * 1.9,
                                                 r * 0.52, r * 3.5), r * 0.26))
            flag = QPainterPath()
            flag.moveTo(cx + r * 0.95, cy - r * 1.85)
            flag.lineTo(cx + r * 2.2, cy - r * 1.25)
            flag.lineTo(cx + r * 2.2, cy - r * 0.35)
            flag.lineTo(cx + r * 0.95, cy - r * 0.95)
            flag.closeSubpath()
            painter.drawPath(flag)
        else:
            painter.setBrush(QBrush(theme.color("#000000", 0.35)))
            painter.drawPath(rounded_path(QRectF(tile.right() - 15, tile.bottom() - 15, 11, 11), 3))
            painter.setPen(QPen(theme.color("#FFFFFF"), 1.2))
            for i, frac in enumerate((0.35, 0.6, 0.85)):
                x = tile.right() - 12.5 + i * 3.0
                painter.drawLine(QPointF(x, tile.bottom() - 5.5),
                                 QPointF(x, tile.bottom() - 5.5 - frac * 5.0))

        # ---- texts ------------------------------------------------------- #
        text_left = tile.right() + 10
        text_width = max(30.0, rect.right() - text_left - 8)
        painter.setFont(theme.font(9, 600))
        title = painter.fontMetrics().elidedText(label or "?", Qt.ElideRight, int(text_width))
        painter.setPen(QPen(theme.color(theme.GREEN) if current else theme.color(theme.TEXT)))
        painter.drawText(QRectF(text_left, rect.center().y() - 19, text_width, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, title)

        painter.setFont(theme.font(8))
        subtitle = "Playlist" if count is None else f"Playlist • {count} song{'' if count == 1 else 's'}"
        subtitle = painter.fontMetrics().elidedText(subtitle, Qt.ElideRight, int(text_width))
        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        painter.drawText(QRectF(text_left, rect.center().y() + 1, text_width, 16),
                         Qt.AlignVCenter | Qt.AlignLeft, subtitle)
        painter.restore()


class OnlineDelegate(QStyledItemDelegate):
    """A search result from the online catalogue, drawn like a Spotify row."""

    TILE = 38.0
    ROW = int(TILE) + 14

    def __init__(self, view: "OnlineResultsView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.ROW)

    def paint(self, painter: QPainter, option, index):
        track = index.data(Qt.UserRole)
        if track is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -4, -1)
        row = index.row()
        current = row == self.view.current_index
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if current or hover or selected:
            alpha = 0.09 if current else (0.07 if hover else 0.05)
            painter.setBrush(QBrush(theme.color("#FFFFFF", alpha)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(rect, 4))

        # ---- cover tile (gradient seeded by the song, no download) ------- #
        tile = QRectF(rect.left() + 6, rect.center().y() - self.TILE / 2,
                      self.TILE, self.TILE)
        first, second = theme.playlist_gradient(track.title or "?")
        grad = QLinearGradient(tile.left(), tile.top(), tile.right(), tile.bottom())
        grad.setColorAt(0.0, theme.color(second))
        grad.setColorAt(1.0, theme.color(first))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(rounded_path(tile, 5))

        if current and self.view.playing:
            painter.setBrush(QBrush(theme.color("#FFFFFF")))
            for i in range(3):
                height = 4 + self.view.bar_heights[i] * 9
                painter.drawPath(rounded_path(
                    QRectF(tile.center().x() - 7 + i * 5.0,
                           tile.center().y() - height / 2, 2.8, height), 1.4))
        else:
            # a small play triangle marks a playable result
            painter.setBrush(QBrush(theme.color("#FFFFFF", 0.9)))
            cx, cy = tile.center().x() + 1, tile.center().y()
            triangle = QPainterPath()
            triangle.moveTo(cx - 4, cy - 6)
            triangle.lineTo(cx + 6, cy)
            triangle.lineTo(cx - 4, cy + 6)
            triangle.closeSubpath()
            painter.drawPath(triangle)

        # ---- text --------------------------------------------------------- #
        badge = self.view.badge_rect(rect)
        glyph = self.view.glyph_rect(rect)
        text_left = tile.right() + 10
        text_width = max(40.0, badge.left() - 10 - text_left)

        saved = track.id in self.view.saved_ids
        progress = self.view.saving.get(track.id)

        painter.setFont(theme.font(10, 600))
        title = painter.fontMetrics().elidedText(track.title, Qt.ElideRight, int(text_width))
        painter.setPen(QPen(theme.color(theme.GREEN if current else theme.TEXT)))
        painter.drawText(QRectF(text_left, rect.top() + 7, text_width, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, title)

        painter.setFont(theme.font(8))
        subtitle = painter.fontMetrics().elidedText(track.subtitle(), Qt.ElideRight,
                                                    int(text_width))
        painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
        painter.drawText(QRectF(text_left, rect.top() + 25, text_width, 15),
                         Qt.AlignVCenter | Qt.AlignLeft, subtitle)

        # ---- state badge: full song / downloading / downloaded ------------ #
        if progress is not None:
            label, tint, alpha = f"DOWNLOAD {int(progress * 100)}%", theme.TEXT, 0.16
        elif saved:
            label, tint, alpha = "DOWNLOADED", theme.GREEN, 0.16
        else:
            label, tint, alpha = "FULL SONG", theme.TEXT_DIM, 0.12
        painter.setBrush(QBrush(theme.color(tint, alpha)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(rounded_path(badge, 10))
        painter.setFont(theme.mono(7, 700))
        painter.setPen(QPen(theme.color(tint)))
        painter.drawText(badge.adjusted(4, 0, -4, 0), Qt.AlignCenter, label)

        # ---- save-offline button (right edge of the row) ------------------ #
        if saved:
            draw_icon(painter, "check", glyph.adjusted(5, 4, -5, -4),
                      theme.color(theme.GREEN), 2.0)
        else:
            if hover:
                painter.setBrush(QBrush(theme.color("#FFFFFF", 0.12)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(glyph.adjusted(0, 0, 0, 0))
            draw_icon(painter, "download", glyph.adjusted(6, 5, -6, -5),
                      theme.color(theme.TEXT if hover else theme.TEXT_MUTED), 1.6)
        painter.restore()


class OnlineResultsView(QListWidget):
    """The "online songs" list (Apple Music search) under the playlist."""

    trackClicked = Signal(int)
    trackContextMenu = Signal(QPoint, int)
    downloadClicked = Signal(int)

    #: width of the badge + the save button at the right edge of a row
    BADGE_W = 98.0
    GLYPH_W = 32.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Online")
        self.tracks: list = []
        self.current_index = -1
        self.playing = False
        self.bar_heights = [0.4, 0.7, 0.5]
        self.message = "Type to search online"
        self.saved_ids: set[int] = set()
        self.saving: dict[int, float] = {}
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(OnlineDelegate(self))
        self.itemClicked.connect(self._on_item_clicked)
        self.itemActivated.connect(self._on_item_clicked)

    def set_tracks(self, tracks: list) -> None:
        self.tracks = list(tracks)
        self.clear()
        from PySide6.QtWidgets import QListWidgetItem
        for track in self.tracks:
            item = QListWidgetItem(track.title)
            item.setData(Qt.UserRole, track)
            self.addItem(item)
        self.current_index = -1
        self.viewport().update()

    def set_message(self, message: str) -> None:
        self.message = message or ""
        self.viewport().update()

    # ---- geometry shared with the delegate and the hit test ------------- #
    def row_rect(self, item) -> QRectF:
        return QRectF(self.visualItemRect(item)).adjusted(2, 1, -4, -1)

    def glyph_rect(self, row: QRectF) -> QRectF:
        row = QRectF(row)
        return QRectF(row.right() - self.GLYPH_W + 2, row.center().y() - 13, 26, 26)

    def badge_rect(self, row: QRectF) -> QRectF:
        row = QRectF(row)
        return QRectF(self.glyph_rect(row).left() - 8 - (self.BADGE_W - 4),
                      row.center().y() - 10, self.BADGE_W - 4, 20)

    # ---- saved state ----------------------------------------------------- #
    def set_saved(self, ids) -> None:
        self.saved_ids = set(ids or ())
        self.viewport().update()

    def set_saving(self, track_id: int, fraction: float) -> None:
        self.saving[int(track_id)] = max(0.0, min(1.0, float(fraction)))
        self.viewport().update()

    def clear_saving(self, track_id: int) -> None:
        self.saving.pop(int(track_id), None)
        self.viewport().update()

    def set_current(self, index: int, playing: bool = False) -> None:
        self.current_index = index
        self.playing = playing
        if 0 <= index < self.count():
            self.setCurrentRow(index)
        self.viewport().update()

    def set_playing(self, playing: bool) -> None:
        if playing != self.playing:
            self.playing = playing
            self.viewport().update()

    def tick(self) -> None:
        if self.playing and 0 <= self.current_index < self.count():
            step = getattr(self, "_phase", 0) + 1
            self._phase = step
            self.bar_heights = [
                0.35 + 0.65 * abs(math.sin(step * 0.28 + i * 1.1)) for i in range(3)
            ]
            self.viewport().update()

    def _on_item_clicked(self, item) -> None:
        self.trackClicked.emit(self.row(item))

    def mouseReleaseEvent(self, event):
        """Clicking the arrow on a row downloads the full song instead of playing it."""
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                track = item.data(Qt.UserRole)
                rect = self.glyph_rect(self.row_rect(item))
                if rect.contains(event.position()) and getattr(track, "id", None) not in self.saved_ids:
                    self.downloadClicked.emit(self.row(item))
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None:
            self.setCurrentItem(item)
            self.trackContextMenu.emit(event.globalPos(), self.row(item))
        event.accept()

    def paintEvent(self, event):
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(theme.color(theme.TEXT_DIM)))
            painter.setFont(theme.font(9))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, self.message)
            painter.end()
            return
        super().paintEvent(event)


class PlaylistView(QListWidget):
    """Virtualised playlist with a fully custom-painted delegate."""

    trackClicked = Signal(int)
    trackContextMenu = Signal(QPoint, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Playlist")
        self.tracks: list = []
        self.current_index = -1
        self.playing = False
        self.liked: frozenset[str] = frozenset()
        self.tint = theme.color(theme.CYAN)
        self.bar_heights = [0.4, 0.7, 0.5]
        self.flash = 0.0
        self.empty_hint = "No songs loaded"
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(PlaylistDelegate(self))
        self.itemClicked.connect(self._on_item_clicked)
        self.itemActivated.connect(self._on_item_clicked)

    # -- data ------------------------------------------------------------- #
    def set_tracks(self, tracks: list) -> None:
        self.tracks = list(tracks)
        self.clear()
        for track in self.tracks:
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(track.display_title)
            item.setData(Qt.UserRole, track)
            item.setData(Qt.UserRole + 1, format_time(track.duration))
            self.addItem(item)
        self.viewport().update()

    def update_track_meta(self, row: int, track) -> None:
        item = self.item(row)
        if item is not None:
            item.setData(Qt.UserRole + 1, format_time(track.duration))
            self.viewport().update()

    def set_current(self, index: int, playing: bool = False) -> None:
        self.current_index = index
        self.playing = playing
        if 0 <= index < self.count():
            self.setCurrentRow(index)
        self.viewport().update()

    def set_playing(self, playing: bool) -> None:
        if playing != self.playing:
            self.playing = playing
            self.viewport().update()

    def set_tint(self, color) -> None:
        self.tint = theme.color(color)
        self.viewport().update()

    def set_liked(self, paths) -> None:
        """Paths shown with Spotify's little green heart."""
        self.liked = frozenset(paths or ())
        self.viewport().update()

    def set_flash(self, value: float) -> None:
        """Short highlight used when the playing song changes (1 -> 0)."""
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self.flash) > 0.01 or value in (0.0, 1.0):
            self.flash = value
            if 0 <= self.current_index < self.count():
                self.viewport().update()

    def tick(self) -> None:
        if self.playing and 0 <= self.current_index < self.count():
            step = self._phase = getattr(self, "_phase", 0) + 1
            self.bar_heights = [
                0.35 + 0.65 * abs(math.sin(step * 0.28 + i * 1.1)) for i in range(3)
            ]
            self.viewport().update()

    def scroll_to_current(self) -> None:
        if 0 <= self.current_index < self.count():
            self.scrollToItem(self.item(self.current_index), QListWidget.PositionAtCenter)

    # -- events ----------------------------------------------------------- #
    def _on_item_clicked(self, item) -> None:
        self.trackClicked.emit(self.row(item))

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None:
            self.setCurrentItem(item)
            self.trackContextMenu.emit(event.globalPos(), self.row(item))
        event.accept()

    def paintEvent(self, event):
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(theme.color(theme.TEXT)))
            painter.setFont(theme.font(11, 700))
            painter.drawText(self.viewport().rect().adjusted(0, -18, 0, 0), Qt.AlignCenter, self.empty_hint)
            painter.setFont(theme.font(9))
            painter.setPen(QPen(theme.color(theme.TEXT_DIM, 0.9)))
            painter.drawText(self.viewport().rect().adjusted(0, 16, 0, 0), Qt.AlignCenter,
                             'Click "Open Folder" to load your music')
            painter.end()
            return
        super().paintEvent(event)


# --------------------------------------------------------------------------- #
#  artwork + meters
# --------------------------------------------------------------------------- #
class ArtworkView(QWidget):
    """Album cover with a soft colour cast taken from the song itself."""

    def __init__(self, size: int = 260, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.accent = theme.color(theme.CYAN)
        self.pulse = 0.0
        self.playing = False
        self.reveal = 1.0
        self._side = size
        self.setFixedSize(size + 44, size + 40)

    def set_art(self, pixmap: QPixmap | None, accent: str | QColor | None = None) -> None:
        self._pixmap = pixmap
        if accent is not None:
            self.accent = theme.color(accent)
        self.update()

    def set_reveal(self, value: float) -> None:
        """0 = just changed song (faded/small), 1 = fully settled."""
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self.reveal) > 0.004:
            self.reveal = value
            self.update()

    def set_accent(self, accent: str | QColor) -> None:
        self.accent = theme.color(accent)
        self.update()

    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        self.update()

    def tick(self, phase: float) -> None:
        self.pulse = phase
        if self.playing:
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = float(self._side)
        grow = (1.0 - self.reveal) * 10.0
        rect = QRectF((self.width() - side) / 2 - grow, (self.height() - side) / 2 + 2 - grow,
                      side + grow * 2, side + grow * 2)
        painter.setOpacity(0.25 + 0.75 * self.reveal)

        strength = 0.16 + (0.06 * (0.5 + 0.5 * math.sin(self.pulse)) if self.playing else 0.0)
        for i in range(6, 0, -1):
            spread = i * 3.0
            alpha = strength * (1.0 - i / 7.0) * 0.6
            painter.setBrush(QBrush(theme.color(self.accent, alpha)))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(rect.adjusted(-spread, -spread, spread, spread), 8 + spread / 2))

        painter.setBrush(QBrush(theme.color(theme.SUNK)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(rounded_path(rect, 8))

        if self._pixmap is not None:
            path = rounded_path(rect, 8)
            painter.save()
            painter.setClipPath(path)
            scaled = self._pixmap.scaled(int(rect.width()), int(rect.height()),
                                         Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(int(rect.left()), int(rect.top()), scaled)
            painter.restore()

            sheen = QLinearGradient(rect.left(), rect.top(), rect.left(),
                                    rect.top() + rect.height() * 0.5)
            sheen.setColorAt(0.0, theme.color("#FFFFFF", 0.06))
            sheen.setColorAt(1.0, theme.color("#FFFFFF", 0.0))
            painter.setBrush(QBrush(sheen))
            painter.setPen(Qt.NoPen)
            painter.drawPath(rounded_path(QRectF(rect.left(), rect.top(), rect.width(),
                                                 rect.height() * 0.5), 8))

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("#FFFFFF", 0.10), 1.0))
        painter.drawPath(rounded_path(rect, 8))
        painter.end()


class ElidedLabel(QLabel):
    """Single-line label that cuts its text with an ellipsis instead of clipping."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        # let the layout squeeze us: the text is elided instead of forcing width
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(32, super().minimumSizeHint().height())

    def setText(self, text) -> None:                       # noqa: N802 - Qt API
        self._full = str(text)
        super().setText(self._elided())

    def _elided(self) -> str:
        width = self.width()
        if width <= 4:
            return self._full
        return QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        elided = self._elided()
        if elided != self.text():
            super().setText(elided)


class CoverThumb(QWidget):
    """The small rounded cover shown in the bottom play bar (Spotify's bar art).

    Falls back to a brand-gradient tile with a music note while nothing plays.
    """

    def __init__(self, size: int = 56, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.accent = theme.color(theme.GREEN)
        self._seed = "Spoti Player"
        self._glyph = "note"
        self.setFixedSize(size, size)

    def set_art(self, pixmap: QPixmap | None, accent: str | QColor | None = None) -> None:
        self._pixmap = pixmap
        if accent is not None:
            self.accent = theme.color(accent)
        self.update()

    def set_placeholder(self, seed: str, glyph: str = "note") -> None:
        """Choose the brand-gradient tile drawn while there is no cover art."""
        seed = seed or "Spoti Player"
        if seed != self._seed or glyph != self._glyph:
            self._seed = seed
            self._glyph = glyph
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5)
        path = rounded_path(rect, 6)
        painter.setPen(Qt.NoPen)
        if self._pixmap is None:
            if self._glyph == "heart":
                # Spotify's Liked Songs tile: purple/blue gradient + white heart
                first, second = "#AF2896", "#509BF5"
            elif self._glyph == "download":
                # Downloads: Spotify's green "downloaded" tile
                first, second = theme.GREEN_LIGHT, theme.GREEN_DEEP
            else:
                first, second = theme.playlist_gradient(self._seed)
            grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            grad.setColorAt(0.0, theme.color(second))
            grad.setColorAt(1.0, theme.color(first))
            painter.setBrush(QBrush(grad))
            painter.drawPath(path)
            painter.setBrush(QBrush(theme.color("#FFFFFF", 0.92)))
            cx, cy = rect.center().x(), rect.center().y()
            if self._glyph == "heart":
                painter.setPen(Qt.NoPen)
                painter.drawPath(heart_path(rect.adjusted(rect.width() * 0.26,
                                                          rect.height() * 0.26,
                                                          -rect.width() * 0.26,
                                                          -rect.height() * 0.26)))
            elif self._glyph == "download":
                draw_icon(painter, "download",
                          rect.adjusted(rect.width() * 0.2, rect.height() * 0.2,
                                        -rect.width() * 0.2, -rect.height() * 0.2),
                          theme.color("#000000"), 2.2)
            else:
                # a white music note, like Spotify's playlist tiles
                r = rect.width() * 0.13
                painter.drawEllipse(QPointF(cx - r * 0.5, cy + r * 1.5), r * 1.25, r)
                painter.drawPath(rounded_path(QRectF(cx + r * 0.45, cy - r * 1.9,
                                                     r * 0.52, r * 3.5), r * 0.26))
                flag = QPainterPath()
                flag.moveTo(cx + r * 0.95, cy - r * 1.85)
                flag.lineTo(cx + r * 2.2, cy - r * 1.25)
                flag.lineTo(cx + r * 2.2, cy - r * 0.35)
                flag.lineTo(cx + r * 0.95, cy - r * 0.95)
                flag.closeSubpath()
                painter.drawPath(flag)
        else:
            painter.save()
            painter.setClipPath(path)
            scaled = self._pixmap.scaled(int(rect.width()), int(rect.height()),
                                         Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(int(rect.left()), int(rect.top()), scaled)
            painter.restore()
            painter.setPen(QPen(theme.color("#FFFFFF", 0.12), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.end()


class PlaylistHero(QFrame):
    """Spotify's playlist page hero: cover, big title, meta and the green play.

    A face for the view the window is already showing — its play / shuffle
    buttons drive the very same transport as the bottom bar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        self._top = theme.color(theme.GREEN_DEEP)

        self.cover = CoverThumb(176)
        self.cover.set_placeholder("All songs")

        self.eyebrow = QLabel("Playlist")
        self.eyebrow.setObjectName("HeroEyebrow")
        self.title = QLabel("All songs")
        self.title.setObjectName("HeroTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("0 songs")
        self.meta.setObjectName("HeroMeta")

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch(1)
        text.addWidget(self.eyebrow)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        text.addSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(24, 22, 24, 4)
        top.setSpacing(26)
        top.addWidget(self.cover, 0, Qt.AlignBottom)
        top.addLayout(text, 1)

        self.play = IconButton("play", 56, theme.GREEN, "Play / Pause (Space)",
                               style="green")
        self.shuffle = IconButton("shuffle", 30, theme.VIOLET, "Shuffle the queue")
        self.shuffle.setCheckable(True)
        actions = QHBoxLayout()
        actions.setContentsMargins(24, 14, 24, 18)
        actions.setSpacing(12)
        actions.addWidget(self.play, 0, Qt.AlignVCenter)
        actions.addWidget(self.shuffle, 0, Qt.AlignVCenter)
        actions.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(top)
        root.addLayout(actions)

    def set_view(self, title: str, eyebrow: str = "Playlist",
                 seed: str = "", glyph: str = "note") -> None:
        """Show what the window is looking at (folder / sub-folder / liked)."""
        title = title or "All songs"
        self.eyebrow.setText(eyebrow)
        self.title.setText(title)
        self.title.setToolTip(title)
        self.cover.set_placeholder(seed or title, glyph)
        if glyph == "heart":
            first = "#AF2896"
        else:
            first, _second = theme.playlist_gradient(seed or title)
        self._top = theme.mix(theme.BG1, first, 0.75)
        self.update()

    def set_meta(self, text: str) -> None:
        self.meta.setText(text)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Spotify's hero: the playlist colour at the top, melting into the panel
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, theme.mix(self._top, "#000000", 0.10))
        grad.setColorAt(0.6, theme.mix(theme.BG1, self._top, 0.35))
        grad.setColorAt(1.0, theme.color(theme.BG1))
        painter.setPen(Qt.NoPen)
        painter.fillPath(rounded_path(rect, 8), QBrush(grad))
        painter.end()


class PlayerBar(QFrame):
    """Spotify's play bar — the dark strip fixed at the bottom of the window.

    It gets a faint hint of the playing song's cover colour at the top edge,
    the same way the panels tint themselves.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent = theme.color(theme.GREEN)
        self.setFixedHeight(92)

    def set_accent(self, accent: str | QColor) -> None:
        self.accent = theme.color(accent)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = rounded_path(rect, 8)
        # Spotify's play bar: a flat near-black strip, separated from the page
        # above only by the gap and a single hairline.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.BG1)))
        painter.drawPath(path)
        painter.setPen(QPen(theme.color(theme.STROKE), 1.0))
        painter.drawLine(QPointF(rect.left() + 12, rect.top() + 0.5),
                         QPointF(rect.right() - 12, rect.top() + 0.5))
        painter.end()


class LogoMark(QWidget):
    """The app's little green circle with three sound bars (Spotify flavoured)."""

    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = float(min(self.width(), self.height()))
        circle = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.GREEN)))
        painter.drawEllipse(circle)
        painter.setBrush(QBrush(theme.color("#000000")))
        bar_w = side * 0.11
        for i, frac in enumerate((0.34, 0.62, 0.44)):
            height = side * frac
            x = circle.center().x() + (i - 1) * side * 0.22 - bar_w / 2
            painter.drawPath(rounded_path(
                QRectF(x, circle.center().y() - height / 2, bar_w, height), bar_w / 2))
        painter.end()


class SpectrumMeter(QWidget):
    """Animated green bars (header + 'now playing' indicator)."""

    def __init__(self, bars: int = 5, parent=None):
        super().__init__(parent)
        self.bars = bars
        self.active = False
        self._phase = 0.0
        self.setFixedSize(bars * 6 + 6, 26)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def tick(self) -> None:
        self._phase += 0.22
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for i in range(self.bars):
            if self.active:
                frac = 0.3 + 0.7 * abs(math.sin(self._phase + i * 0.85))
            else:
                frac = 0.18 + 0.06 * (i % 3)
            height = 4 + frac * 18
            x = 4 + i * 6
            color = theme.color(theme.GREEN, 0.55 + 0.45 * (i / max(1, self.bars))) \
                if not self.active else theme.color(theme.GREEN)
            painter.setBrush(QBrush(color))
            painter.drawPath(rounded_path(QRectF(x, (26 - height) / 2, 3.5, height), 1.75))
        painter.end()
