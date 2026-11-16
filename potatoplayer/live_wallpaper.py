"""Locally generated "live wallpapers" for the radio mini bar.

The mini bar used to show a random picture behind the radio.  These scenes
replace it: every frame is painted with ``QPainter`` from the station family,
seeded by the station key, so the background is animated, looks different for
each station of the same family and is *generated on the fly* - nothing is
downloaded, bundled or cached.

The scenes are cheap on purpose: a handful of gradients, arcs and dots on a
500x70 strip, repainted at ~16 fps only while the radio is on screen.
"""

from __future__ import annotations

import math
import zlib

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)

#: station category -> the animated scene painted behind the mini bar
SCENE_BY_CATEGORY = {
    "Chill & Lofi": "rain",
    "Relax & Vocals": "bokeh",
    "Ambient & Space": "space",
    "Electronic": "neon",
    "Indie & Rock": "embers",
    "Jazz & Soul": "bokeh",
    "World & Eclectic": "aurora",
    "Synthwave": "retro",
}


def scene_for(category: str) -> str:
    """The scene name for a station category (space when unknown)."""
    return SCENE_BY_CATEGORY.get(category, "space")


def paint(painter: QPainter, rect: QRectF, scene: str, key: str, t: float) -> None:
    """Paint one frame of ``scene``; ``t`` is seconds since the scene started."""
    seed = zlib.crc32((key or scene).encode("utf-8")) & 0xFFFFFFFF
    painter.save()
    _SCENES.get(scene, _space)(painter, rect, seed, t)
    painter.restore()


# --------------------------------------------------------------------------- #
#  shared helpers
# --------------------------------------------------------------------------- #
def _fill(painter: QPainter, rect: QRectF, top: str, bottom: str) -> None:
    grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    painter.setPen(Qt.NoPen)
    painter.fillRect(rect, QBrush(grad))


def _glow(painter: QPainter, cx: float, cy: float, radius: float, color: QColor) -> None:
    """A soft radial blob (nebula, window light, spotlight...)."""
    if radius <= 0:
        return
    grad = QRadialGradient(QPointF(cx, cy), radius)
    grad.setColorAt(0.0, color)
    grad.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(),
                                int(color.alpha() * 0.45)))
    edge = QColor(color)
    edge.setAlpha(0)
    grad.setColorAt(1.0, edge)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawEllipse(QPointF(cx, cy), radius, radius)


def _hue(seed: int, base: float, spread: float = 36.0) -> float:
    """Shift a hue by the station seed so siblings look related, not equal."""
    return (base + ((seed % 1000) / 1000.0 - 0.5) * spread) % 360.0


def _hsv(hue: float, sat: float, val: float, alpha: float = 1.0) -> QColor:
    color = QColor.fromHsvF((hue % 360.0) / 360.0, sat, val)
    color.setAlpha(int(max(0.0, min(1.0, alpha)) * 255))
    return color


def _star_field(painter: QPainter, rect: QRectF, seed: int, t: float,
                count: int = 46, alpha: int = 150) -> None:
    width, height = rect.width(), rect.height()
    for i in range(count):
        x = rect.left() + (i * 197 + seed) % (width + 1)
        y = rect.top() + (i * 89 + (seed >> 8)) % (height + 1)
        twinkle = 0.5 + 0.5 * math.sin(t * (0.7 + (i % 5) * 0.22) + i * 1.3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255,
                                       int(alpha * (0.35 + 0.65 * twinkle)))))
        radius = 0.7 + (i % 3) * 0.35
        painter.drawEllipse(QPointF(x, y), radius, radius)


# --------------------------------------------------------------------------- #
#  scenes
# --------------------------------------------------------------------------- #
def _rain(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Chill & Lofi: a rainy window with warm room light behind it."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#070d18", "#16202f")

    # warm lamp light from the bottom-left, cool light from above
    _glow(painter, rect.left() + width * 0.2, rect.top() + height * 1.05,
          height * 1.15, QColor(255, 146, 70, 60))
    _glow(painter, rect.left() + width * 0.62, rect.top() + height * 1.2,
          height * 0.95, QColor(90, 140, 255, 38))

    # drifting mist
    for i in range(3):
        x = rect.left() + (width * (0.12 + 0.34 * i)
                           + math.sin(t * 0.18 + i * 1.7) * 34) % (width + 80) - 40
        _glow(painter, x, rect.top() + height * (0.5 + 0.12 * i),
              46 + i * 14, QColor(120, 160, 220, 18))

    # rain streaks, each drop with its own speed
    for i in range(30):
        speed = 120 + (i * 53) % 110
        x = (i * 71 + seed % 113 + t * speed * 0.4) % (width + 40) - 20 + rect.left()
        y = (i * 41 + t * speed) % (height + 30) - 15 + rect.top()
        length = 6 + i % 10
        painter.setPen(QPen(QColor(190, 215, 255, 35 + (i * 13) % 45), 1.0))
        painter.drawLine(QPointF(x, y), QPointF(x - length * 0.4, y + length))

    # little warm city lights along the bottom edge
    hue = _hue(seed, 32, 30)
    painter.setPen(Qt.NoPen)
    for i in range(8):
        x = rect.left() + width * (i + 0.5) / 8
        y = rect.top() + height - 6 - (i * 7) % 10
        painter.setBrush(QBrush(_hsv(hue, 0.55, 1.0, 0.35)))
        painter.drawEllipse(QPointF(x, y), 1.4, 1.4)


def _space(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Ambient & Space: slow nebula clouds and a twinkling star field."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#03050e", "#0d0a20")

    for i, base in enumerate((225.0, 285.0, 200.0)):
        hue = _hue(seed, base, 50)
        cx = rect.left() + (width * (0.16 + 0.3 * i)
                            + math.sin(t * 0.12 + i * 1.9) * 42) % width
        cy = rect.top() + height * (0.34 + 0.15 * i) + math.cos(t * 0.1 + i) * 8
        _glow(painter, cx, cy, 58 + i * 18, _hsv(hue, 0.65, 0.7, 0.17))

    _star_field(painter, rect, seed, t)

    # a shooting star every ~9 seconds
    cycle = (t + (seed % 700) / 100.0) % 9.0
    if cycle < 0.55:
        prog = cycle / 0.55
        x = rect.left() + width * 1.05 - prog * width * 1.25
        y = rect.top() + height * 0.18 + prog * height * 0.5
        fade = math.sin(prog * math.pi)
        painter.setPen(QPen(QColor(255, 255, 255, int(170 * fade)), 1.4))
        painter.drawLine(QPointF(x, y), QPointF(x + 17, y - 8))


def _neon(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Electronic: a spectrum wall pulsing behind a moving scanline."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#02070b", "#06161c")
    hue = _hue(seed, 155, 70)

    bars = 30
    bar_w = width / bars
    for i in range(bars):
        level = 0.25 + 0.75 * abs(math.sin(t * 1.5 + i * 0.55 + (seed % 17) * 0.3))
        bar_h = height * 0.58 * level
        painter.fillRect(QRectF(rect.left() + i * bar_w + 1,
                                rect.top() + height - bar_h, bar_w - 2, bar_h),
                         _hsv(hue + i * 1.5, 0.75, 1.0, 0.13 + 0.18 * level))

    scan_y = rect.top() + (t * 34) % (height + 12) - 6
    scan = QLinearGradient(0, scan_y - 9, 0, scan_y + 9)
    scan.setColorAt(0.0, QColor(120, 255, 220, 0))
    scan.setColorAt(0.5, QColor(120, 255, 220, 70))
    scan.setColorAt(1.0, QColor(120, 255, 220, 0))
    painter.fillRect(QRectF(rect.left(), scan_y - 9, width, 18), QBrush(scan))

    _glow(painter, rect.left() + width * 0.5, rect.top() + height * 1.15,
          height * 0.85, _hsv(hue, 0.6, 1.0, 0.1))


def _embers(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Indie & Rock: warm spotlight, fast streaks and rising embers."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#140805", "#2a100a")
    hue = _hue(seed, 18, 30)

    _glow(painter, rect.left() + width * 0.5 + math.sin(t * 0.2) * 42,
          rect.top() + height * 1.1, height * 1.3, _hsv(hue, 0.8, 0.9, 0.16))

    for i in range(16):
        speed = 40 + (i * 29) % 60
        x = (i * 83 + t * speed) % (width + 120) - 60 + rect.left()
        y = (i * 37 - t * speed * 0.45) % (height + 40) - 20 + rect.top()
        length = 24 + (i % 5) * 14
        painter.setPen(QPen(_hsv(hue + 8, 0.7, 1.0, (16 + (i * 7) % 22) / 255), 1.0))
        painter.drawLine(QPointF(x, y), QPointF(x + length, y - length * 0.42))

    painter.setPen(Qt.NoPen)
    for i in range(20):
        x = rect.left() + (i * 59 + seed % 41 + math.sin(t * 0.5 + i) * 8) % width
        y = rect.top() + height + 10 - ((t * (10 + i % 13) + i * 23) % (height + 20))
        painter.setBrush(QBrush(_hsv(hue, 0.6, 1.0, (40 + (i * 11) % 50) / 255)))
        radius = 0.8 + (i % 3) * 0.4
        painter.drawEllipse(QPointF(x, y), radius, radius)


def _bokeh(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Jazz & Soul: a warm spotlight and slow, out-of-focus light circles."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#120a13", "#22101a")
    hue = _hue(seed, 330, 50)

    _glow(painter, rect.left() + width * (0.3 + 0.1 * math.sin(t * 0.15)),
          rect.top() + height * 0.5, height * 0.95, _hsv(hue, 0.5, 0.9, 0.14))

    for i in range(16):
        x = rect.left() + (i * 89 + seed % 71
                           + math.sin(t * 0.25 + i * 1.3) * 18) % width
        y = rect.top() + (i * 47 + (seed >> 5) % 53
                          + math.cos(t * 0.2 + i) * 9) % height
        radius = 3 + (i * 7) % 11
        alpha = (22 + (i * 13) % 40) / 255
        circle_hue = hue + i * 9
        painter.setPen(QPen(_hsv(circle_hue, 0.45, 1.0, alpha), 1.0))
        painter.setBrush(QBrush(_hsv(circle_hue, 0.5, 1.0, alpha * 0.4)))
        painter.drawEllipse(QPointF(x, y), radius, radius)


def _aurora(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """World & Eclectic: slow green/teal/violet aurora ribbons."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#03100e", "#071a1b")

    _star_field(painter, rect, seed, t, count=26, alpha=90)

    for band, base in enumerate((150.0, 175.0, 280.0)):
        hue = _hue(seed, base, 40)
        path = QPainterPath()
        steps = 30
        for i in range(steps + 1):
            x = rect.left() + width * i / steps
            y = (rect.top() + height * (0.32 + 0.12 * band)
                 + math.sin(x * 0.02 + t * 0.55 + band * 1.9) * height * 0.11)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        for pen_width, alpha in ((14, 18), (8, 30), (3, 55)):
            painter.setPen(QPen(_hsv(hue, 0.65, 1.0, alpha / 255), pen_width,
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(path)


def _retro(painter: QPainter, rect: QRectF, seed: int, t: float) -> None:
    """Synthwave: a neon sun over a scrolling perspective grid."""
    width, height = rect.width(), rect.height()
    _fill(painter, rect, "#070311", "#190a2b")
    hue = _hue(seed, 315, 40)

    cx = rect.left() + width * 0.76
    cy = rect.top() + height * 0.55
    radius = height * 0.46
    for scale, alpha in ((1.0, 55), (0.7, 80), (0.4, 110)):
        _glow(painter, cx, cy, radius * scale, _hsv(hue, 0.8, 1.0, alpha / 255))

    horizon = rect.top() + height * 0.68
    floor = QLinearGradient(0, horizon, 0, rect.top() + height)
    floor.setColorAt(0.0, QColor(10, 2, 20, 235))
    floor.setColorAt(1.0, QColor(3, 0, 8, 245))
    painter.fillRect(QRectF(rect.left(), horizon, width, rect.top() + height - horizon),
                     QBrush(floor))

    painter.setPen(QPen(_hsv(hue, 0.75, 1.0, 0.35), 1.0))
    for i in range(5):
        frac = ((i / 5.0) + (t * 0.12)) % 1.0
        y = horizon + (rect.top() + height - horizon) * frac * frac
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    painter.setPen(QPen(QColor(90, 220, 255, 75), 1.0))
    for i in range(-6, 7):
        x_bottom = rect.left() + width / 2 + i * (width / 6)
        painter.drawLine(QPointF(rect.left() + width / 2, horizon),
                         QPointF(x_bottom, rect.top() + height))

    painter.setPen(QPen(QColor(255, 120, 220, 120), 1.4))
    painter.drawLine(QPointF(rect.left(), horizon), QPointF(rect.right(), horizon))


_SCENES = {
    "rain": _rain,
    "space": _space,
    "neon": _neon,
    "embers": _embers,
    "bokeh": _bokeh,
    "aurora": _aurora,
    "retro": _retro,
}
