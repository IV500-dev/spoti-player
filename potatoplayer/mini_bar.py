"""Floating mini bar: song title + cover, pinned on top of every window.

Design notes
------------
* The bar is frameless, always-on-top and **never takes focus**
  (``WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE``), and it keeps its
  place at the very top of the topmost band by re-asserting
  ``SetWindowPos(HWND_TOPMOST)`` **followed by** ``SetWindowPos(HWND_TOP)`` -
  the second call is the one that actually climbs, because ``HWND_TOPMOST`` is a
  no-op for a window that is already topmost.  If something manages to cover the
  bar it notices on its own and re-pins itself in a short fast burst; there is
  nothing to switch on.
* Only its own controls are clickable: everywhere else the window gets
  ``WS_EX_TRANSPARENT``, so clicks fall through to whatever is underneath.  The
  Windows title-bar buttons (close / minimise / maximise) of the app below and
  the game stay fully usable, and the bar never "eats" a click.
* It is **pinned to the top centre** of the screen and cannot be dragged;
  only its own buttons react — the rest of the bar ignores the mouse.
* Animations (slide in, track-change flash) are skipped in performance mode.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import live_wallpaper, theme
from .widgets import draw_icon, rounded_path

BAR_WIDTH = 500
BAR_HEIGHT = 70
COVER = 52
BUTTON = 30
BUTTONS = ("play", "next", "close")
#: radio mode replaces "next" with "stop" (stop beats radio off)
RADIO_BUTTONS = ("play", "stop", "close")
#: bee-yellow bars of the radio equalizer
BEE = "#FFD400"

# --- win32 constants -------------------------------------------------------- #
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
HWND_TOP = 0
GW_HWNDPREV = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400
GA_ROOT = 2

#: re-assert intervals (ms): steady, performance mode, and the burst used while
#: the bar is being covered by something
INTERVAL_BASE = 700
INTERVAL_PERFORMANCE = 1200
INTERVAL_BURST = 60
BURST_SECONDS = 6.0
MAX_MISSES = 8
CURSOR_POLL_MS = 120


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MiniBar(QWidget):
    """Compact always-on-top now-playing bar."""

    playPauseRequested = Signal()
    nextRequested = Signal()
    previousRequested = Signal()
    closeRequested = Signal()
    stopRequested = Signal()
    restoreRequested = Signal()
    topmostBlocked = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Spoti Player — mini bar")
        self.setFixedSize(BAR_WIDTH, BAR_HEIGHT)
        self.setWindowOpacity(0.96)
        self.setMouseTracking(True)

        self.accent = theme.color(theme.CYAN)
        self.playing = False
        self.progress = 0.0
        self.title = "Nothing playing"
        self.artist = "Spoti Player"
        self._cover: QPixmap | None = None
        self._wallpaper: QPixmap | None = None   # optional still backdrop
        # radio mode: locally generated animated scene (no downloaded images)
        self._live_scene: str | None = None
        self._live_key = ""
        self._live_t0 = time.monotonic()
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(60)          # ~16 fps, enough for slow scenes
        self._live_timer.timeout.connect(self.update)
        self.radio_mode = False
        # pseudo-equalizer bars for the radio (visual only)
        self._eq_levels = [0.0] * 14
        self._eq_timer = QTimer(self)
        self._eq_timer.setInterval(110)
        self._eq_timer.timeout.connect(self._animate_eq)
        self._hover = -1
        self.performance = False        # minimal work: no animation, slow timers
        self.blocked = False            # something (exclusive fullscreen) owns the screen
        self._misses = 0
        self._burst_until = 0.0
        self._click_through = False
        self._flash = QVariantAnimation(self)
        self._flash.setDuration(750)
        self._flash.setStartValue(0.0)
        self._flash.setEndValue(1.0)
        self._flash.setEasingCurve(QEasingCurve.OutCubic)
        self._flash.valueChanged.connect(self._on_flash)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(220)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)

        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(INTERVAL_BASE)
        self._topmost_timer.timeout.connect(self._maintain_topmost)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(CURSOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._poll_cursor)

    # ------------------------------------------------------------------ data #
    def set_track(self, title: str, artist: str, accent: str | QColor | None = None) -> None:
        title = title or "Nothing playing"
        changed = title != self.title
        self.title = title
        self.artist = artist or ""
        if accent is not None:
            self.accent = theme.color(accent)
        if changed and not self.performance and self.isVisible():
            self._flash.stop()
            self._flash.setStartValue(1.0)
            self._flash.setEndValue(0.0)
            self._flash.start()
        self.update()

    def set_accent(self, accent: str | QColor) -> None:
        """Follow the tint of the playing song (synced by the main window)."""
        color = theme.color(accent)
        if color != self.accent:
            self.accent = color
            self.update()

    def set_cover(self, pixmap: QPixmap | None) -> None:
        self._cover = pixmap
        self.update()

    def set_wallpaper(self, pixmap: QPixmap | None) -> None:
        """Optional still picture behind the whole bar (cover is hidden)."""
        self._wallpaper = pixmap
        self.update()

    def set_live_wallpaper(self, scene: str | None, key: str = "") -> None:
        """Radio mode: a locally generated, animated scene behind the bar.

        ``key`` seeds the palette, so every station gets its own variation.
        """
        if scene == self._live_scene and key == self._live_key:
            return
        self._live_scene = scene or None
        self._live_key = key
        self._live_t0 = time.monotonic()
        self._sync_live_timer()
        self.update()

    def _sync_live_timer(self) -> None:
        """Animate the live wallpaper only while the radio bar is on screen."""
        running = (bool(self._live_scene) and self.radio_mode
                   and self.isVisible() and not self.performance)
        if running and not self._live_timer.isActive():
            self._live_timer.start()
        elif not running and self._live_timer.isActive():
            self._live_timer.stop()

    def set_playing(self, playing: bool) -> None:
        if playing != self.playing:
            self.playing = playing
            self._sync_eq_timer()
            self.update()

    def set_radio_mode(self, radio: bool) -> None:
        """Radio layout: title top-left, bee equalizer, stop button."""
        if radio == self.radio_mode:
            return
        self.radio_mode = bool(radio)
        self._sync_eq_timer()
        self._sync_live_timer()
        self.update()

    def _sync_eq_timer(self) -> None:
        running = self.radio_mode and self.playing and self.isVisible() and not self.performance
        if running and not self._eq_timer.isActive():
            self._eq_timer.start()
        elif not running and self._eq_timer.isActive():
            self._eq_timer.stop()

    def _animate_eq(self) -> None:
        """Nudge the equalizer bars towards new random levels."""
        import random
        for i in range(len(self._eq_levels)):
            target = random.uniform(0.18, 1.0)
            current = self._eq_levels[i]
            self._eq_levels[i] = current + (target - current) * 0.55
        self.update()

    def set_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        threshold = 0.02 if self.performance else 0.0015
        if abs(fraction - self.progress) > threshold:
            self.progress = fraction
            self.update()

    def set_performance(self, performance: bool) -> None:
        self.performance = bool(performance)
        self._flash.stop()
        self._flash.setCurrentTime(self._flash.duration())
        self._update_interval()
        self._sync_live_timer()
        self.update()

    def _on_flash(self, value) -> None:
        self._flash_value = float(value)
        self.update()

    # -------------------------------------------------------------- geometry #
    def place_top_centre(self) -> None:
        """Top centre of the screen under the cursor (falls back to primary)."""
        screen = None
        try:
            from PySide6.QtGui import QCursor
            screen = QApplication.screenAt(QCursor.pos())
        except Exception:
            screen = None
        if screen is None:
            screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
        self.move(area.left() + (area.width() - self.width()) // 2, area.top() + 10)

    def _slide_in(self) -> None:
        """Little drop-in animation when the bar appears (unless performance mode)."""
        if self.performance:
            return
        target = self.pos()
        self._slide.stop()
        self._slide.setStartValue(QPoint(target.x(), target.y() - 46))
        self._slide.setEndValue(target)
        self._slide.start()

    # ------------------------------------------------------------------ win32 #
    def _hwnd(self) -> int:
        try:
            return int(self.winId())
        except Exception:
            return 0

    def _update_interval(self) -> None:
        """Steady re-assert rate, faster in a short burst, slower in performance mode."""
        if time.time() < self._burst_until:
            interval = INTERVAL_BURST
        elif self.performance:
            interval = INTERVAL_PERFORMANCE
        else:
            interval = INTERVAL_BASE
        if self._topmost_timer.interval() != interval:
            self._topmost_timer.setInterval(interval)

    def _burst(self, seconds: float = BURST_SECONDS) -> None:
        self._burst_until = max(self._burst_until, time.time() + seconds)
        self._update_interval()

    def _assert_topmost(self, hard: bool = False) -> None:
        hwnd = self._hwnd()
        if not hwnd or not self.isVisible():
            return
        try:
            user32 = ctypes.windll.user32
            if hard:
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                wanted = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                if ex_style & wanted != wanted:
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | wanted)
            flags = (SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                     | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags | SWP_SHOWWINDOW)
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, flags)
        except Exception:
            pass

    def _apply_extended_styles(self) -> None:
        hwnd = self._hwnd()
        if not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex_style |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
        except Exception:
            pass

    def set_click_through(self, enabled: bool) -> None:
        """Let clicks fall through to the app below (used outside our controls)."""
        if enabled == self._click_through:
            return
        hwnd = self._hwnd()
        if not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                style &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            self._click_through = enabled
        except Exception:
            pass

    def is_on_top(self) -> bool:
        """True when no foreign window sits above the bar and overlaps it."""
        hwnd = self._hwnd()
        if not hwnd or not self.isVisible():
            return True
        try:
            user32 = ctypes.windll.user32
            ours = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(ours))
            previous = user32.GetWindow(hwnd, GW_HWNDPREV)
            guard = 0
            while previous and guard < 200:
                guard += 1
                if user32.IsWindowVisible(previous):
                    other = _RECT()
                    user32.GetWindowRect(previous, ctypes.byref(other))
                    overlaps = not (other.right <= ours.left or other.left >= ours.right
                                    or other.bottom <= ours.top or other.top >= ours.bottom)
                    if overlaps:
                        owner = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(previous, ctypes.byref(owner))
                        if owner.value != os.getpid():
                            return False
                previous = user32.GetWindow(previous, GW_HWNDPREV)
            return True
        except Exception:
            return True

    def _maintain_topmost(self) -> None:
        """Keep the bar pinned; if something covers it, fight back for a while."""
        self._assert_topmost()
        if not self.is_on_top():
            self._misses += 1
            if self._misses <= MAX_MISSES:
                self._burst(2.0)
                self._assert_topmost(hard=True)
            elif not self.blocked:
                self.blocked = True
                self.topmostBlocked.emit()
                self._update_interval()
        else:
            if self._misses:
                self._misses = 0
            if self.blocked:
                self.blocked = False
        self._update_interval()

    # ----------------------------------------------------------------- events #
    def showEvent(self, event):
        super().showEvent(event)
        self._apply_extended_styles()
        self._assert_topmost(hard=True)
        self._burst()
        self._update_interval()
        self._topmost_timer.start()
        self._cursor_timer.start()
        self._sync_eq_timer()
        self._sync_live_timer()
        self._slide_in()

    def hideEvent(self, event):
        self._topmost_timer.stop()
        self._cursor_timer.stop()
        self._eq_timer.stop()
        self._live_timer.stop()
        self.set_click_through(False)
        super().hideEvent(event)

    # ------------------------------------------------------------ mouse areas #
    def _buttons(self) -> tuple:
        return RADIO_BUTTONS if self.radio_mode else BUTTONS

    def _button_rects(self) -> list[QRect]:
        rects = []
        right = self.width() - 12
        for _ in self._buttons():
            rects.append(QRect(right - BUTTON, (self.height() - BUTTON) // 2, BUTTON, BUTTON))
            right -= BUTTON + 6
        rects.reverse()
        return rects

    def _cover_rect(self) -> QRect:
        return QRect(12, (self.height() - COVER) // 2, COVER, COVER)

    def _interactive_rects(self) -> list[QRect]:
        return self._button_rects() + [self._cover_rect()]

    def _button_at(self, pos: QPoint) -> int:
        for index, rect in enumerate(self._button_rects()):
            if rect.contains(pos):
                return index
        return -1

    def _cover_at(self, pos: QPoint) -> bool:
        return self._cover_rect().contains(pos)

    def _poll_cursor(self) -> None:
        """Hover feedback + click-through: only our own controls catch the mouse."""
        if not self.isVisible():
            return
        try:
            from PySide6.QtGui import QCursor
            local = self.mapFromGlobal(QCursor.pos())
        except Exception:
            return
        inside = self.rect().contains(local) and (
            self._button_at(local) >= 0 or self._cover_at(local))
        self.set_click_through(not inside)
        hover = self._button_at(local) if self.rect().contains(local) else -1
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._button_at(event.position().toPoint()) >= 0:
            self.update()

    def mouseMoveEvent(self, event):
        # the bar is pinned top-centre — it cannot be dragged around
        pass

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        index = self._button_at(event.position().toPoint())
        if index >= 0:
            kind = self._buttons()[index]
            self._hover = -1
            self.update()
            if kind == "play":
                self.playPauseRequested.emit()
            elif kind == "next":
                self.nextRequested.emit()
            elif kind == "stop":
                self.stopRequested.emit()
            else:
                self.closeRequested.emit()

    def mouseDoubleClickEvent(self, event):
        if self._button_at(event.position().toPoint()) < 0 and not self._cover_at(
                event.position().toPoint()):
            self.restoreRequested.emit()

    def wheelEvent(self, event):
        if event.angleDelta().y() < 0:
            self.nextRequested.emit()
        else:
            self.previousRequested.emit()

    def leaveEvent(self, event):
        # hover is owned by the cursor poll (the window is click-through most of
        # the time, so Qt's enter/leave events are not reliable here)
        super().leaveEvent(event)

    # ------------------------------------------------------------------- draw #
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        flash = getattr(self, "_flash_value", 0.0)

        # radio mode: a live scene (or an optional still picture) fills the bar
        live_scene = self._live_scene if self.radio_mode else None
        wallpaper = self._wallpaper if not live_scene else None
        backdrop = bool(live_scene) or (wallpaper is not None and not wallpaper.isNull())
        if backdrop:
            painter.save()
            painter.setClipPath(rounded_path(rect, 16))
            if live_scene:
                live_wallpaper.paint(painter, rect, live_scene, self._live_key,
                                     time.monotonic() - self._live_t0)
            else:
                scaled = wallpaper.scaled(int(self.width()), int(self.height()),
                                          Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation)
                painter.drawPixmap((self.width() - scaled.width()) // 2,
                                   (self.height() - scaled.height()) // 2, scaled)
            painter.restore()
            # dark veil so the text stays readable on any artwork
            veil = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            veil.setColorAt(0.0, QColor(0, 0, 0, 100))
            veil.setColorAt(1.0, QColor(0, 0, 0, 30))
            painter.setBrush(QBrush(veil))
            painter.setPen(QPen(theme.color(self.accent, 0.75), 1.4))
            painter.drawPath(rounded_path(rect, 16))
            if flash > 0.01:
                painter.setBrush(QBrush(theme.color(self.accent, 0.28 * flash)))
                painter.setPen(Qt.NoPen)
                painter.drawPath(rounded_path(rect, 16))
        else:
            grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            grad.setColorAt(0.0, theme.color(theme.mix(theme.BG2, self.accent, 0.16), 0.97))
            grad.setColorAt(1.0, theme.color(theme.mix(theme.BG1, self.accent, 0.05), 0.97))
            painter.setBrush(QBrush(grad))
            painter.setPen(QPen(theme.color(self.accent, 0.75), 1.4))
            painter.drawPath(rounded_path(rect, 16))

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(theme.color(self.accent, 0.9)))
            painter.drawPath(rounded_path(QRectF(rect.left() + 1, rect.top() + 12,
                                                  3, rect.height() - 24), 1.5))

            if flash > 0.01:
                painter.setBrush(QBrush(theme.color(self.accent, 0.28 * flash)))
                painter.drawPath(rounded_path(rect, 16))

        # cover (also the drag handle) — hidden while a radio backdrop shows
        cover_rect = QRectF(self._cover_rect())
        if not backdrop:
            painter.setBrush(QBrush(theme.color(theme.SUNK)))
            painter.drawPath(rounded_path(cover_rect, 10))
            if self._cover is not None and not self._cover.isNull():
                painter.save()
                painter.setClipPath(rounded_path(cover_rect, 10))
                scaled = self._cover.scaled(int(COVER), int(COVER),
                                            Qt.KeepAspectRatioByExpanding,
                                            Qt.SmoothTransformation)
                painter.drawPixmap(int(cover_rect.left()), int(cover_rect.top()), scaled)
                painter.restore()
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.color(self.accent, 0.5), 1.0))
            painter.drawPath(rounded_path(cover_rect, 10))

        # texts — soft text shadow when the wallpaper is behind them
        if self.radio_mode:
            # radio: song title sits at the top-left of the whole bar
            text_left = rect.left() + 14
            text_right = self._button_rects()[0].left() - 10
            width = max(60, int(text_right - text_left))

            title_font = theme.font(10, 700)
            painter.setFont(title_font)
            painter.setPen(QPen(QColor(0, 0, 0, 180)))
            title = QFontMetrics(title_font).elidedText(self.title, Qt.ElideRight, width)
            painter.drawText(QRectF(text_left + 1, rect.top() + 9, width, 18),
                             Qt.AlignVCenter | Qt.AlignLeft, title)
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.drawText(QRectF(text_left, rect.top() + 8, width, 18),
                             Qt.AlignVCenter | Qt.AlignLeft, title)

            # bee-yellow equalizer bars under the title
            painter.setPen(Qt.NoPen)
            eq_font = theme.font(8)
            painter.setFont(eq_font)
            painter.setPen(QPen(QColor(255, 255, 255, 170)))
            painter.drawText(QRectF(text_left, rect.top() + 24, width, 12),
                             Qt.AlignVCenter | Qt.AlignLeft,
                             QFontMetrics(eq_font).elidedText(
                                 self.artist or "Radio", Qt.ElideRight, width))
            painter.setPen(Qt.NoPen)
            eq_top = rect.top() + 40
            eq_h = rect.height() - 40 - 12
            bar_w = 4.0
            gap = 4.0
            x = text_left
            bee = QColor(BEE)
            for level in self._eq_levels:
                if self.playing:
                    h = max(4.0, eq_h * min(1.0, level))
                else:
                    h = 4.0
                bar = QRectF(x, eq_top + eq_h - h, bar_w, h)
                painter.setBrush(QBrush(bee))
                painter.drawPath(rounded_path(bar, 2))
                x += bar_w + gap
            return

        text_left = cover_rect.right() + 12
        text_right = self._button_rects()[0].left() - 10
        width = max(40, int(text_right - text_left))
        title_font = theme.font(10, 700)
        painter.setFont(title_font)
        if wallpaper is not None and not wallpaper.isNull():
            painter.setPen(QPen(QColor(0, 0, 0, 170)))
            painter.drawText(QRectF(text_left + 1, rect.top() + 11, width, 18),
                             Qt.AlignVCenter | Qt.AlignLeft, title)
        painter.setPen(QPen(theme.color(theme.TEXT)))
        title = QFontMetrics(title_font).elidedText(self.title, Qt.ElideRight, width)
        painter.drawText(QRectF(text_left, rect.top() + 10, width, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, title)

        painter.setFont(theme.font(8))
        if wallpaper is not None and not wallpaper.isNull() and not self.blocked:
            painter.setPen(QPen(QColor(0, 0, 0, 160)))
            painter.drawText(QRectF(text_left + 1, rect.top() + 29, width, 14),
                             Qt.AlignVCenter | Qt.AlignLeft, artist)
        painter.setPen(QPen(theme.color(theme.TEXT if self.blocked else theme.TEXT_DIM)))
        artist_text = self.artist or "Spoti Player"
        if self.blocked:
            artist_text += "   •   hidden by a fullscreen game — use Borderless mode"
        artist = QFontMetrics(theme.font(8)).elidedText(artist_text, Qt.ElideRight, width)
        painter.drawText(QRectF(text_left, rect.top() + 28, width, 14),
                         Qt.AlignVCenter | Qt.AlignLeft, artist)

        # progress
        track = QRectF(text_left, rect.top() + 48, width, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.SUNK)))
        painter.drawPath(rounded_path(track, 2))
        if self.progress > 0:
            played = QRectF(track.left(), track.top(), track.width() * self.progress, track.height())
            grad2 = QLinearGradient(played.left(), 0, track.right(), 0)
            grad2.setColorAt(0.0, theme.color(theme.VIOLET))
            grad2.setColorAt(1.0, self.accent)
            painter.setBrush(QBrush(grad2))
            painter.drawPath(rounded_path(played, 2))

        # buttons: play / next|stop / close
        for index, button_rect in enumerate(self._button_rects()):
            name = self._buttons()[index]
            hovered = index == self._hover
            accent = theme.ROSE if name in ("close", "stop") else self.accent
            kind = "pause" if (name == "play" and self.playing) else name
            if hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(theme.color(accent, 0.22)))
                painter.drawPath(rounded_path(QRectF(button_rect), BUTTON / 2))
            color = theme.lighten(accent, 0.35) if hovered else theme.color(theme.TEXT_DIM)
            icon_box = BUTTON * 0.46
            draw_icon(painter, kind,
                      QRectF(button_rect.center().x() - icon_box / 2,
                             button_rect.center().y() - icon_box / 2, icon_box, icon_box),
                      color)
        painter.end()
