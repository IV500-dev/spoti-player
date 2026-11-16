"""Spoti Player main window — Qt 6 implementation of the whole player."""

from __future__ import annotations

import os
import queue
import random
import subprocess
import sys
import threading
import time
import webbrowser

from PySide6.QtCore import QEasingCurve, QPoint, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import (artwork, config, discord_rpc, engine as engine_mod, library, live_wallpaper,
               fulltrack, online, saved, single_instance, theme)
from . import winint
from .library import SORT_MODES, Track, format_time
from .mini_bar import MiniBar
from . import radio
from .widgets import (
    ArtworkView, Card, Chip, CoverThumb, DropOverlay, ElidedLabel, IconButton, LikedRow,
    LogoMark, OnlineDelegate, OnlineResultsView, Panel, PillButton, PlaylistHero, PlayerBar,
    PlaylistHeader, PlaylistTileDelegate, PlaylistView, SearchField, SectionHeader, SeekBar,
    SpectrumMeter, TopBar, VolumeBar, pil_to_pixmap,
)

PANEL_W_LEFT = 398
PANEL_W_RIGHT = 336
ART_SIZE = 264


class SpotiPlayerWindow(QMainWindow):
    def __init__(self, initial_folder: str | None = None, vlc_dir: str | None = None,
                 initial_files: list[str] | None = None):
        super().__init__()
        self.cfg = config.load()
        if vlc_dir:
            self.cfg["vlc_dir"] = vlc_dir
        self.art = artwork.ArtworkLoader(config.cache_dir())

        self.engine = engine_mod.VlcEngine(self.cfg.get("vlc_dir") or None,
                                           volume=self.cfg.get("volume", 70))
        self.engine.set_muted(bool(self.cfg.get("muted", False)))
        # the equalizer was removed from the player: keep the audio untouched
        self.engine.set_eq_enabled(False)

        # ---- state -------------------------------------------------------- #
        self.source: list[Track] = []
        self.queue: list[Track] = []
        self.current_index = -1
        self.current_track: Track | None = None
        self.repeat = self.cfg.get("repeat", "all")
        self.shuffle = bool(self.cfg.get("shuffle", False))
        self.sort_mode = self.cfg.get("sort_mode", SORT_MODES[0])
        self._manual_order = False
        self._playing = False
        self._seeking = False
        self._end_handled = False
        self._error_streak = 0
        self._phase = 0.0
        self._ui_queue: queue.Queue = queue.Queue()
        self._scan_token = 0
        self._meta_running = False
        self._device_pairs: list[tuple[str, str]] = []
        self._art_request: str | None = None
        self._tint = theme.color(theme.GREEN)
        self.liked: set[str] = {str(p) for p in self.cfg.get("liked", []) if p}
        self._liked_only = False
        self._history: list[dict] = []
        self._history_index = -1
        # files handed over by a double-click / a second launch: play them as
        # soon as their folder has been scanned
        self.initial_files = [os.path.abspath(p) for p in (initial_files or [])]
        self._pending_play = self.initial_files[0] if self.initial_files else None
        self._pending_extra = list(self.initial_files[1:])
        self._ipc_check = 0.0
        self.mini_bar: MiniBar | None = None
        self._mini_cover_pixmap = None
        # radio state (the station key + the playing song of a song-list station)
        self.radio_mode = False
        self.radio_station = (radio.station(self.cfg.get("radio_station"))
                              or radio.station(radio.DEFAULT_STATION)).key
        self.radio_song: str | None = None
        self.radio_queue: list[str] = []
        # online search (Apple Music catalogue): results, the playing preview and
        # a debounce timer so typing does not fire one request per keystroke
        self.online_results: list[online.OnlineTrack] = []
        self.online_track: online.OnlineTrack | None = None
        self._online_index = -1
        self._online_token = 0
        self._online_art_request = None
        # full songs downloaded from the online search (the "Downloads" playlist)
        self.saved: list[Track] = saved.tracks()
        self._saved_only = False
        self._saved_ids: set[int] = saved.ids()
        self._saving: dict[int, float] = {}
        self._online_timer = QTimer(self)
        self._online_timer.setSingleShot(True)
        self._online_timer.setInterval(420)
        self._online_timer.timeout.connect(self._search_online)
        self._closing = False
        self.performance_mode = bool(self.cfg.get("performance_mode", False))
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.lbl_status.setText(""))

        self.presence = discord_rpc.DiscordPresence(
            client_id=self.cfg.get("discord_client_id", ""),
            enabled=bool(self.cfg.get("discord_enabled", True)),
            large_image=self.cfg.get("discord_large_image", "spoti"),
        )

        self.setWindowTitle("Spoti Player — Spotify-styled music player")
        self.setWindowIcon(self._build_icon())
        self.setMinimumSize(1130, 720)
        self.setAcceptDrops(True)
        self._build_ui()
        self._bind_keys()
        self._restore_geometry()
        self._build_tray()

        self._refresh_devices(select_saved=True)
        self._sync_volume_ui()
        self.playlist.set_liked(self.liked)
        self._sync_like_ui()
        self._sync_liked_row()
        self._sync_saved_row()
        self.online_list.set_saved(self._saved_ids)
        self._remember_state()
        self.presence.start()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000 if self.performance_mode else 250)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        if not self.performance_mode:
            self._anim_timer.start(90)
        self._tint_anim = QVariantAnimation(self)
        self._tint_anim.setDuration(520)
        self._tint_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._tint_anim.valueChanged.connect(self._on_tint_step)
        self._reveal_anim = QVariantAnimation(self)
        self._reveal_anim.setDuration(420)
        self._reveal_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._reveal_anim.setStartValue(0.0)
        self._reveal_anim.setEndValue(1.0)
        self._reveal_anim.valueChanged.connect(self._on_reveal_step)
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(700)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.valueChanged.connect(self._on_flash_step)

        folder = initial_folder or self.cfg.get("folder") or ""
        if not folder and self.initial_files:
            folder = os.path.dirname(self.initial_files[0])
        if folder and os.path.isdir(folder):
            self.load_folder(folder)
        elif folder:
            self.set_status(f"Folder not found: {folder} — use Open Folder to pick another one")
        else:
            self.set_status("Ready — open a folder to build your library")

        if self.performance_mode:
            self._apply_performance_mode(True)

        # the mini bar was left open the last time -> bring it back
        if self.cfg.get("mini_bar"):
            QTimer.singleShot(500, self.show_mini_bar)

    # ===================================================================== #
    #  window chrome
    # ===================================================================== #
    def _build_icon(self) -> QIcon:
        try:
            return QIcon(pil_to_pixmap(artwork.render_app_icon(96)))
        except Exception:
            return QIcon()

    def _restore_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        width = min(1380, (available.width() - 60) if available else 1380)
        height = min(930, (available.height() - 60) if available else 900)
        geometry = self.cfg.get("geometry") or ""
        applied = False
        if geometry:
            try:
                parts = geometry.replace("x", "+").split("+")
                w, h = int(parts[0]), int(parts[1])
                x, y = int(parts[2]), int(parts[3])
                if available is not None:
                    w = max(1130, min(w, available.width()))
                    h = max(720, min(h, available.height()))
                    # these are *client* coordinates: leave room for the title bar
                    x = max(available.left() - w + 220, min(x, available.right() - 220))
                    y = max(available.top() + 40, min(y, available.bottom() - 160))
                self.setGeometry(x, y, w, h)
                applied = True
            except (ValueError, IndexError):
                applied = False
        if not applied:
            if available is not None:
                x = available.left() + (available.width() - width) // 2
                y = available.top() + max(0, (available.height() - height) // 3)
            else:
                x = y = 60
            self.setGeometry(x, y, width, height)
        self.setMinimumSize(1130, 720)

    def _ensure_on_screen(self) -> bool:
        """Make sure the title bar (close/minimise buttons) is always reachable."""
        try:
            screen = self.screen() or QApplication.primaryScreen()
            area = screen.availableGeometry()
            frame = self.frameGeometry()
            dx = dy = 0
            if frame.top() < area.top() + 1:
                dy = area.top() + 1 - frame.top()
            elif frame.bottom() > area.bottom() - 1:
                dy = area.bottom() - 1 - frame.bottom()
            if frame.right() < area.left() + 220:
                dx = area.left() + 220 - frame.right()
            elif frame.left() > area.right() - 220:
                dx = area.right() - 220 - frame.left()
            if dx or dy:
                self.move(self.x() + dx, self.y() + dy)
                return True
        except Exception:
            pass
        return False

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_geometry_checked", False):
            self._geometry_checked = True
            self._ensure_on_screen()
            QTimer.singleShot(120, self._ensure_on_screen)   # again once the frame exists
        if getattr(self, "drop_overlay", None) is not None:
            self.drop_overlay.setGeometry(self.centralWidget().rect())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "drop_overlay", None) is not None:
            self.drop_overlay.setGeometry(self.centralWidget().rect())
            if self.drop_overlay.isVisible():
                self.drop_overlay.raise_()

    # ===================================================================== #
    #  layout
    # ===================================================================== #
    def _build_ui(self) -> None:
        central = QWidget()
        # NOTE: never give the central widget a bare `background` stylesheet —
        # Qt copies it onto every child widget, which paints slabs over the
        # panels. The window background comes from the QMainWindow rule in
        # theme.stylesheet() instead.
        central.setObjectName("Central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        outer.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setSpacing(8)
        # Spotify's desktop layout: black library sidebar | playlist | now playing
        left = Panel(theme.GREEN, base="black")
        left.setFixedWidth(PANEL_W_LEFT)
        center = Panel(theme.GREEN)
        right = Panel(theme.GREEN, base="black")
        right.setFixedWidth(PANEL_W_RIGHT)
        body.addWidget(left)
        body.addWidget(center, 1)
        body.addWidget(right)
        outer.addLayout(body, 1)

        self._build_library(left)
        self._build_playlist(center)
        self._build_player(right)

        self.drop_overlay = DropOverlay(central)
        self.drop_overlay.setGeometry(central.rect())
        self.drop_overlay.raise_()
        outer.addWidget(self._build_statusbar())
        # Spotify's play bar is the last thing in the window: bottom edge
        outer.addWidget(self._build_player_bar())

    def _build_topbar(self) -> TopBar:
        """Spotify's green top bar: navigation, the search box and the chips."""
        self.topbar = TopBar()
        self.meter = SpectrumMeter(6)
        self.topbar.add(self.meter)
        self.topbar.add(LogoMark(28))
        self.lbl_app = QLabel("Spoti Player")
        self.lbl_app.setObjectName("AppName")
        self.lbl_app.setStyleSheet("color: #FFFFFF; background: transparent; font-weight: 800;")
        self.topbar.add(self.lbl_app)

        self.btn_back = IconButton("back", 30, style="dark", tooltip="Back")
        self.btn_back.clicked.connect(self.go_back)
        self.btn_forward = IconButton("forward", 30, style="dark", tooltip="Forward")
        self.btn_forward.clicked.connect(self.go_forward)
        self.btn_home = IconButton("home", 30, style="white", tooltip="Home — all songs")
        self.btn_home.clicked.connect(self.go_home)
        self.topbar.add(self.btn_back)
        self.topbar.add(self.btn_forward)
        self.topbar.add(self.btn_home)
        self._sync_nav_buttons()

        self.search = SearchField()
        self.search.setPlaceholderText("What do you want to play?")
        self.search.setAcceptDrops(False)          # let folder drops reach the window
        self.search.textChanged.connect(self._on_search_changed)
        self.topbar.add(self.search, 1)

        self.chip_folder = Chip("no folder", theme.GREEN)
        self.chip_tracks = Chip("0 tracks", theme.EMERALD)
        self.chip_device = Chip(self.engine.device_name, theme.GREEN)
        for chip in (self.chip_folder, self.chip_tracks, self.chip_device):
            self.topbar.add(chip)
        self.btn_perf = PillButton("Performance", theme.GREEN, "ghost", icon="bolt", height=24,
                                   font_size=8,
                                   tooltip="Performance mode: stop every animation, refresh once a\n"
                                           "second, minimal CPU/GPU use (also makes the mini bar lighter)")
        self.btn_perf.setCheckable(True)
        self.btn_perf.setChecked(self.performance_mode)
        self.btn_perf.clicked.connect(self.toggle_performance_mode)
        self.topbar.add(self.btn_perf)
        self.btn_settings = IconButton("cog", 30, theme.TEXT_DIM,
                                       "Settings  (Ctrl+,)\n"
                                       "Discord, default music player, start with Windows")
        self.btn_settings.clicked.connect(self.open_settings)
        self.topbar.add(self.btn_settings)
        self.btn_quit = IconButton("power", 30, theme.ROSE,
                                   "Quit Spoti Player  (Ctrl+Q)\n"
                                   "Closing with ✕ only hides to the tray while a song is playing")
        self.btn_quit.clicked.connect(self.quit_app)
        self.topbar.add(self.btn_quit)
        return self.topbar

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 0, 6, 0)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Status")
        row.addWidget(self.lbl_status)
        row.addStretch(1)
        hint = QLabel("Space play/pause    ←/→ seek    P/N track    M mute    L like    Ctrl+O open folder")
        hint.setObjectName("Hint")
        row.addWidget(hint)
        bar.setFixedHeight(22)
        return bar

    # --------------------------------------------------------------- library #
    def _build_library(self, parent: Panel) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = SectionHeader("Your Library", theme.GREEN)
        self.btn_open = PillButton("Open Folder", theme.GREEN, "solid", icon="folder", height=30)
        self.btn_open.clicked.connect(self.choose_folder)
        header.add_right(self.btn_open)
        layout.addWidget(header)

        # ---- pinned "Liked Songs" playlist, like Spotify's sidebar ----
        self.liked_row = LikedRow()
        self.liked_row.clicked.connect(lambda: self.show_liked_songs())
        layout.addWidget(self.liked_row)

        # ---- full songs downloaded from the online search (playable offline) -- #
        self.saved_row = LikedRow(
            title="Downloads", kind="download",
            tooltip="Downloads — full songs saved on this PC",
            gradient=(theme.GREEN_LIGHT, theme.GREEN_DEEP))
        self.saved_row.clicked.connect(lambda: self.show_downloads())
        layout.addWidget(self.saved_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        sort_label = QLabel("Sort by")
        sort_label.setObjectName("Hint")
        controls.addWidget(sort_label)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_MODES)
        self.sort_combo.setCurrentText(self.sort_mode if self.sort_mode in SORT_MODES else SORT_MODES[0])
        self.sort_combo.currentTextChanged.connect(self._on_sort_changed)
        controls.addWidget(self.sort_combo, 1)
        layout.addLayout(controls)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        order_label = QLabel("Order")
        order_label.setObjectName("Hint")
        tools.addWidget(order_label)
        self.btn_up = IconButton("up", 28, theme.GREEN, "Move up")
        self.btn_down = IconButton("down", 28, theme.GREEN, "Move down")
        self.btn_rescan = IconButton("refresh", 28, theme.GREEN_LIGHT, "Rescan folder")
        self.btn_clear = IconButton("clear", 28, theme.ROSE, "Clear playlist")
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_rescan.clicked.connect(lambda: self.load_folder(self.cfg.get("folder", "")))
        self.btn_clear.clicked.connect(self.clear_playlist)
        for button in (self.btn_up, self.btn_down, self.btn_rescan):
            tools.addWidget(button)
        tools.addStretch(1)
        tools.addWidget(self.btn_clear)
        layout.addLayout(tools)

        # ---- playlists: every sub-folder is drawn as a Spotify playlist ----
        # (Spotify's sidebar rows sit straight on the black panel — no card)
        self.sub_card = QWidget()
        sub_layout = QVBoxLayout(self.sub_card)
        sub_layout.setContentsMargins(0, 6, 0, 0)
        sub_layout.setSpacing(6)
        self.sub_head = SectionHeader("Your Playlists", theme.GREEN)
        sub_layout.addWidget(self.sub_head)

        self.sub_list = QListWidget()
        self.sub_list.setObjectName("Playlists")
        self.sub_list.setMinimumHeight(PlaylistTileDelegate.ROW * 2 + 4)
        self.sub_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sub_list.setSelectionMode(QListWidget.SingleSelection)
        self.sub_list.setUniformItemSizes(True)
        self.sub_list.setMouseTracking(True)
        self.sub_list.setItemDelegate(PlaylistTileDelegate(self.sub_list))
        self.sub_list.currentRowChanged.connect(self._on_subfolder_chosen)
        sub_layout.addWidget(self.sub_list, 1)

        self.lbl_sub_hint = QLabel("Every folder plays like a playlist. «All songs» is the whole "
                                   "library; pick a folder to hear only that one.")
        self.lbl_sub_hint.setObjectName("Hint")
        self.lbl_sub_hint.setWordWrap(True)
        sub_layout.addWidget(self.lbl_sub_hint)
        self.sub_card.setVisible(False)
        layout.addWidget(self.sub_card, 1)

        self._sub_timer = QTimer(self)
        self._sub_timer.setSingleShot(True)
        self._sub_timer.setInterval(220)
        self._sub_timer.timeout.connect(self._rescan_current_folder)
        self._sub_rows: list[tuple[str, str]] = []       # (label, path or "" for all)
        self._sub_folder = ""

        self.lbl_engine = QLabel(f"libVLC {self.engine.version()}   •   WASAPI output")
        self.lbl_engine.setObjectName("Hint")
        layout.addWidget(self.lbl_engine)

    # -------------------------------------------------------------- playlist #
    def _build_playlist(self, parent: Panel) -> None:
        """The main view: Spotify's playlist page (hero + track list + count)."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        # Spotify's playlist hero: flush with the panel edges — cover, big
        # title and the green play on the fading playlist gradient
        self.hero = PlaylistHero()
        self.hero.play.clicked.connect(self.toggle_play)
        self.hero.shuffle.clicked.connect(self.toggle_shuffle)
        self.hero.shuffle.setChecked(self.shuffle)
        layout.addWidget(self.hero)

        table = QVBoxLayout()
        table.setContentsMargins(16, 10, 16, 0)
        table.setSpacing(8)
        table.addWidget(PlaylistHeader())
        self.playlist = PlaylistView()
        self.playlist.setAcceptDrops(False)
        self.playlist.trackClicked.connect(self.play_index)
        self.playlist.trackContextMenu.connect(self._playlist_menu)
        table.addWidget(self.playlist, 1)

        # ---- online songs (search the catalogue, play or download a song) -- #
        self.online_card = QWidget()
        online_layout = QVBoxLayout(self.online_card)
        online_layout.setContentsMargins(0, 4, 0, 0)
        online_layout.setSpacing(4)
        online_head = SectionHeader("Online Songs", theme.GREEN)
        online_badge = QLabel("APPLE MUSIC  •  FULL-SONG DOWNLOAD")
        online_badge.setObjectName("Hint")
        online_head.add_right(online_badge)
        online_layout.addWidget(online_head)
        self.online_list = OnlineResultsView()
        # the search view gives the results up to three rows; on a short window
        # the list scrolls inside one row instead of being clipped
        self.online_list.setMinimumHeight(OnlineDelegate.ROW + 6)
        self.online_list.setMaximumHeight(OnlineDelegate.ROW * 3 + 8)
        self.online_list.setToolTip("Click a row to play its 30-second preview.\n"
                                    "Click the arrow to download the whole song.\n"
                                    "Right-click → more options for this song.")
        self.online_list.trackClicked.connect(self.play_online)
        self.online_list.downloadClicked.connect(self.download_online)
        self.online_list.trackContextMenu.connect(self._online_menu)
        online_layout.addWidget(self.online_list)
        self.online_card.setVisible(False)
        table.addWidget(self.online_card)

        self.lbl_count = QLabel("0 songs")
        self.lbl_count.setObjectName("Count")
        table.addWidget(self.lbl_count)
        layout.addLayout(table, 1)

    # ---------------------------------------------------------------- player #
    def _build_player(self, parent: Panel) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Spotify's now-playing view announces itself with a small header
        layout.addWidget(SectionHeader("Now Playing", theme.GREEN))

        art_row = QHBoxLayout()
        art_row.addStretch(1)
        self.artwork = ArtworkView(ART_SIZE)
        art_row.addWidget(self.artwork)
        art_row.addStretch(1)
        layout.addLayout(art_row)

        # Spotify: the title is left-aligned, with the heart at the far right
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.lbl_title = QLabel("Nothing playing")
        self.lbl_title.setObjectName("NowTitle")
        self.lbl_title.setWordWrap(True)
        self.btn_np_like = IconButton("heart", 26, theme.GREEN,
                                      "Add to Liked Songs / remove from Liked Songs  (L)")
        self.btn_np_like.clicked.connect(lambda: self.toggle_like())
        title_row.addWidget(self.lbl_title, 1)
        title_row.addWidget(self.btn_np_like, 0, Qt.AlignTop)

        self.lbl_artist = QLabel("—")
        self.lbl_artist.setObjectName("NowArtist")
        self.lbl_artist.setWordWrap(True)
        self.lbl_album = QLabel("")
        self.lbl_album.setObjectName("NowAlbum")
        self.lbl_album.setWordWrap(True)
        self.lbl_tech = QLabel("")
        self.lbl_tech.setObjectName("NowTech")
        self.now_block = QWidget()
        self.now_block.setAttribute(Qt.WA_TranslucentBackground, True)
        self.now_block.setAutoFillBackground(False)
        self.now_block.setStyleSheet("background: transparent;")
        now_layout = QVBoxLayout(self.now_block)
        now_layout.setContentsMargins(0, 0, 0, 0)
        now_layout.setSpacing(1)
        now_layout.addLayout(title_row)
        now_layout.addWidget(self.lbl_artist)
        now_layout.addWidget(self.lbl_album)
        now_layout.addWidget(self.lbl_tech)
        self._now_opacity = QGraphicsOpacityEffect(self.now_block)
        self._now_opacity.setOpacity(1.0)
        self.now_block.setGraphicsEffect(self._now_opacity)
        layout.addWidget(self.now_block)

        # ---- output device ----
        device_card = Card(theme.GREEN)
        dlayout = QVBoxLayout(device_card)
        dlayout.setContentsMargins(14, 10, 14, 10)
        dlayout.setSpacing(6)
        dhead = SectionHeader("Output Device", theme.GREEN)
        self.btn_dev_refresh = IconButton("refresh", 24, theme.TEXT_DIM, "Rescan audio outputs")
        self.btn_dev_refresh.clicked.connect(self._refresh_devices)
        dhead.add_right(self.btn_dev_refresh)
        dlayout.addWidget(dhead)
        self.device_combo = QComboBox()
        self.device_combo.currentTextChanged.connect(self._on_device_selected)
        dlayout.addWidget(self.device_combo)
        self.device_combo.setToolTip("Switching output applies instantly, even mid-song.")
        layout.addWidget(device_card)
        layout.addStretch(1)

    # ------------------------------------------------------------- play bar #
    def _build_player_bar(self) -> QWidget:
        """Spotify's play bar, pinned to the bottom edge of the window.

        Left: cover + title/artist + ♥.  Middle: transport + seek + times.
        Right: stop / mini-bar buttons and the volume drive.
        """
        bar = PlayerBar()
        self.player_bar = bar
        root = QHBoxLayout(bar)
        root.setContentsMargins(18, 8, 18, 8)
        root.setSpacing(14)

        # ---- left: cover, song, heart ---------------------------------- #
        left = QHBoxLayout()
        left.setSpacing(10)
        self.bar_cover = CoverThumb(56)
        left.addWidget(self.bar_cover, 0, Qt.AlignVCenter)
        info = QVBoxLayout()
        info.setSpacing(1)
        self.bar_title = ElidedLabel("Nothing playing")
        self.bar_title.setObjectName("BarTitle")
        self.bar_artist = ElidedLabel("—")
        self.bar_artist.setObjectName("BarArtist")
        info.addWidget(self.bar_title)
        info.addWidget(self.bar_artist)
        info.setAlignment(Qt.AlignVCenter)
        left.addLayout(info, 1)
        self.btn_like = IconButton("heart", 30, theme.GREEN,
                                   "Add to Liked Songs / remove from Liked Songs  (L)")
        self.btn_like.clicked.connect(lambda: self.toggle_like())
        left.addWidget(self.btn_like, 0, Qt.AlignVCenter)
        root.addLayout(left, 1)

        # ---- middle: transport + progress ----------------------------- #
        center = QVBoxLayout()
        center.setSpacing(1)
        center.addStretch(1)
        transport = QHBoxLayout()
        transport.setSpacing(8)
        transport.addStretch(1)
        self.btn_shuffle = IconButton("shuffle", 28, theme.VIOLET, "Shuffle the queue")
        self.btn_shuffle.setCheckable(True)
        self.btn_shuffle.setChecked(self.shuffle)
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_prev = IconButton("prev", 34, theme.TEXT_DIM, "Previous (P)")
        self.btn_prev.clicked.connect(lambda: self.next_track(-1))
        self.btn_play = IconButton("play", 46, theme.CYAN, "Play / Pause (Space)",
                                   style="white")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next = IconButton("next", 34, theme.TEXT_DIM, "Next (N)")
        self.btn_next.clicked.connect(lambda: self.next_track(1))
        self.btn_repeat = IconButton("repeat", 28, theme.EMERALD, "Repeat: off → all → one")
        self.btn_repeat.setCheckable(True)
        self.btn_repeat.clicked.connect(self.cycle_repeat)
        for button in (self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next,
                       self.btn_repeat):
            transport.addWidget(button, 0, Qt.AlignVCenter)
        transport.addStretch(1)
        center.addLayout(transport)

        times = QHBoxLayout()
        times.setSpacing(8)
        self.lbl_now = QLabel("0:00")
        self.lbl_now.setObjectName("Time")
        self.lbl_total = QLabel("-:--")
        self.lbl_total.setObjectName("Time")
        self.seek = SeekBar()
        self.seek.scrubStarted.connect(lambda: setattr(self, "_seeking", True))
        self.seek.scrubbed.connect(self._seek_preview)
        self.seek.committed.connect(self._seek_committed)
        times.addWidget(self.lbl_now, 0, Qt.AlignVCenter)
        times.addWidget(self.seek, 1)
        times.addWidget(self.lbl_total, 0, Qt.AlignVCenter)
        center.addLayout(times)
        center.addStretch(1)
        root.addLayout(center, 2)

        # ---- right: utilities + volume drive --------------------------- #
        right = QHBoxLayout()
        right.setSpacing(6)
        self.btn_stop = IconButton("stop", 28, theme.ROSE, "Stop  (Esc)")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_mini = IconButton("mini", 28, theme.GREEN,
                                   "Mini bar on top — small always-on-top bar with cover + title\n"
                                   "(shows over other apps and borderless-fullscreen games)")
        self.btn_mini.setCheckable(True)
        self.btn_mini.clicked.connect(self.toggle_mini_bar)
        self.btn_radio = IconButton("radio", 28, theme.GREEN,
                                    f"Radio — {len(radio.STATIONS)} internet stations "
                                    "(lofi.radio, SomaFM, FIP, Radio Paradise, Nightride FM…)\n"
                                    "Click to pick a station; right-click to switch while it "
                                    "plays. The mini bar gets a live scene for the station.")
        self.btn_radio.setCheckable(True)
        self.btn_radio.clicked.connect(self.toggle_radio)
        self.btn_radio.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn_radio.customContextMenuRequested.connect(self._radio_context_menu)
        right.addWidget(self.btn_stop, 0, Qt.AlignVCenter)
        right.addWidget(self.btn_radio, 0, Qt.AlignVCenter)
        right.addWidget(self.btn_mini, 0, Qt.AlignVCenter)
        right.addSpacing(8)
        self.btn_mute = IconButton("volume", 26, theme.EMERALD, "Mute (M)")
        self.btn_mute.clicked.connect(self.toggle_mute)
        right.addWidget(self.btn_mute, 0, Qt.AlignVCenter)
        self.volume = VolumeBar()
        self.volume.valueChangedByUser.connect(self._volume_changed)
        self.volume.setMinimumWidth(90)
        self.volume.setMaximumWidth(150)
        right.addWidget(self.volume, 1, Qt.AlignVCenter)
        self.lbl_volume = QLabel("70 %")
        self.lbl_volume.setFont(theme.mono(9, 700))
        self.lbl_volume.setStyleSheet(f"color: {theme.EMERALD};")
        self.lbl_volume.setMinimumWidth(66)
        self.lbl_volume.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.addWidget(self.lbl_volume, 0, Qt.AlignVCenter)
        root.addLayout(right, 1)

        return bar

    # ===================================================================== #
    #  keyboard
    # ===================================================================== #
    def _shortcut(self, sequence: str, handler) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.WindowShortcut)
        shortcut.activated.connect(lambda: self._guarded(handler))

    def _guarded(self, handler) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QComboBox)):
            return
        handler()

    def _bind_keys(self) -> None:
        self._shortcut("Space", self.toggle_play)
        self._shortcut("Left", lambda: self._nudge(-5000))
        self._shortcut("Right", lambda: self._nudge(5000))
        self._shortcut("Up", lambda: self._nudge_volume(5))
        self._shortcut("Down", lambda: self._nudge_volume(-5))
        self._shortcut("N", lambda: self.next_track(1))
        self._shortcut("P", lambda: self.next_track(-1))
        self._shortcut("M", self.toggle_mute)
        self._shortcut("S", self.toggle_shuffle)
        self._shortcut("L", self.toggle_like)
        self._shortcut("Delete", self.remove_selected)
        self._shortcut("Escape", self.stop)
        self._shortcut("Ctrl+O", self.choose_folder)
        self._shortcut("Ctrl+F", lambda: self.search.setFocus())
        self._shortcut("Ctrl+,", self.open_settings)
        self._shortcut("Ctrl+Q", self.quit_app)
        self._shortcut("Ctrl+W", self.quit_app)

    # ===================================================================== #
    #  drag & drop
    # ===================================================================== #
    @staticmethod
    def _dropped_paths(mime) -> tuple[list[str], list[str]]:
        """Split a drop into (folders, audio files)."""
        folders, files = [], []
        if not mime.hasUrls():
            return folders, files
        for url in mime.urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                folders.append(path)
            elif os.path.isfile(path):
                files.append(path)
        return folders, files

    def _drag_paths(self, event) -> tuple[list[str], list[str]]:
        folders, files = self._dropped_paths(event.mimeData())
        return folders, files

    def dragEnterEvent(self, event):
        folders, files = self._drag_paths(event)
        if not folders and not files:
            event.ignore()
            return
        if folders:
            text = (f"Load folder: {os.path.basename(folders[0])}\n"
                    f"{len(folders)} folder(s) + {len(files)} song(s) dropped")
            accent = theme.VIOLET
        else:
            text = (f"Add {len(files)} song(s) to the playlist\n"
                    f"Drop anywhere in this window")
            accent = theme.GREEN
        self.drop_overlay.set_message(text, accent)
        self.drop_overlay.setGeometry(self.centralWidget().rect())
        self.drop_overlay.show()
        self.drop_overlay.raise_()
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        folders, files = self._drag_paths(event)
        event.acceptProposedAction() if (folders or files) else event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_overlay.hide()
        event.accept()

    def dropEvent(self, event):
        self.drop_overlay.hide()
        folders, files = self._dropped_paths(event.mimeData())
        if not folders and not files:
            event.ignore()
            return
        event.acceptProposedAction()
        if folders:
            self.load_folder(folders[0])
            for extra in folders[1:]:
                self.add_folder_files(extra)
        if files:
            self.add_files(files)
        if folders and not files:
            self.set_status(f"Folder dropped: {os.path.basename(folders[0])}")

    def add_files(self, paths: list[str]) -> None:
        """Append loose audio files to the playlist (drag & drop of songs)."""
        audio = [os.path.normpath(p) for p in paths
                 if os.path.splitext(p)[1].lower() in library.AUDIO_EXTS]
        if not audio:
            self.set_status("Nothing to add — those files are not audio")
            return
        known = {track.path for track in self.source}
        added = [Track(path=p) for p in audio if p not in known]
        if not added:
            self.set_status("Those songs are already in the playlist")
            return
        self.source.extend(added)
        self.rebuild_queue(keep_current=True)
        self._start_metadata_worker(added)
        self.set_status(f"Added {len(added)} song{'s' if len(added) != 1 else ''} to the playlist")

    def add_folder_files(self, folder: str) -> None:
        """Add every song of an extra dropped folder to the playlist."""
        try:
            self.add_files([t.path for t in library.scan_folder(folder, True)])
        except Exception as exc:               # pragma: no cover
            self.set_status(f"Could not read {folder}: {exc}")

    # ===================================================================== #
    #  library
    # ===================================================================== #
    def choose_folder(self) -> None:
        start = self.cfg.get("folder") or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Choose your music folder", start)
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder: str) -> None:
        folder = os.path.normpath(folder) if folder else ""
        if not folder or not os.path.isdir(folder):
            self.set_status("Folder not found — pick another one")
            return
        changed = self.cfg.get("folder") != folder
        self.cfg["folder"] = folder
        self.cfg["last_folder"] = folder
        self._populate_subfolders(folder, reset=changed)
        self._remember_state()
        only_dirs = self._selected_subdirs()
        root_files = only_dirs is None          # a chosen sub-folder means *only* that folder
        self._scan_token += 1
        token = self._scan_token
        config.save(self.cfg)
        self.set_status(f"Scanning {folder} …")
        self.chip_folder.set_chip(self._short_folder(folder))

        def worker():
            try:
                tracks = library.scan_folder(folder, True, only_dirs, root_files)
            except Exception as exc:                       # pragma: no cover
                tracks = []
                self._ui_queue.put(("error", f"Scan failed: {exc}"))
            self._ui_queue.put(("scan_done", (token, tracks)))

        threading.Thread(target=worker, daemon=True, name="scan").start()

    # ------------------------------------------------------- sub-folder picker #
    def _populate_subfolders(self, folder: str, reset: bool = False) -> None:
        """Fill the picker with 'All songs' + one row per sub-folder.

        The whole card stays hidden when the folder has no sub-folders at all.
        """
        try:
            subs = library.list_subfolders(folder)
        except Exception:                                  # pragma: no cover
            subs = []
        total = sum(count for _name, _path, count in subs)
        try:
            total = len(library.scan_folder(folder)) if subs else 0
        except Exception:
            pass

        self._sub_folder = folder
        self._sub_rows = [("All songs", "")]
        for name, path, _count in subs:
            self._sub_rows.append((name, path))

        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        for index, (label, path) in enumerate(self._sub_rows):
            count = total if index == 0 else next((c for n, p, c in subs if p == path), 0)
            item = QListWidgetItem(f"{label}   ({count})")
            item.setData(Qt.UserRole, path)
            item.setData(Qt.UserRole + 1, label)
            item.setData(Qt.UserRole + 2, count)
            item.setToolTip(path or "Every song in this folder, including its sub-folders")
            self.sub_list.addItem(item)

        wanted = "All songs"
        if not reset and self.cfg.get("subfolders_for") == folder:
            wanted = self.cfg.get("subfolder_choice") or "All songs"
        row = next((i for i, (label, _p) in enumerate(self._sub_rows) if label == wanted), 0)
        self.sub_list.setCurrentRow(row)
        self._refresh_sub_markers()
        self.sub_list.blockSignals(False)

        self.sub_head.label.setText(
            f"Your Playlists ({len(subs) + 1})" if subs else "Your Playlists")
        self.sub_card.setVisible(bool(subs))
        if not subs:
            self.cfg["subfolders_for"] = folder
            self.cfg["subfolder_choice"] = "All songs"

    def _refresh_sub_markers(self) -> None:
        """Radio-style markers so the current choice is obvious."""
        for row, item in enumerate(self.sub_list.findItems("*", Qt.MatchWildcard)):
            chosen = row == self.sub_list.currentRow()
            text = item.text()
            for glyph in ("◉  ", "○  "):
                if text.startswith(glyph):
                    text = text[len(glyph):]
            item.setText(("◉  " if chosen else "○  ") + text)

    def _chosen_subfolder(self) -> str:
        """'' means 'all songs', otherwise the path of the chosen sub-folder."""
        row = self.sub_list.currentRow()
        if 0 <= row < len(self._sub_rows):
            return self._sub_rows[row][1]
        return ""

    def _on_subfolder_chosen(self, _row: int) -> None:
        self._refresh_sub_markers()
        choice = self._chosen_subfolder() or "All songs"
        label = next((label for label, path in self._sub_rows if path == self._chosen_subfolder()),
                     "All songs")
        self.cfg["subfolders_for"] = self.cfg.get("folder", "")
        self.cfg["subfolder_choice"] = label
        config.save(self.cfg)
        self._remember_state()
        self.set_status(f"Playing the «{label}» playlist…")
        self._sub_timer.start()

    def _selected_subdirs(self) -> list[str] | None:
        """None = every song; otherwise only the chosen sub-folder is scanned."""
        if not self._sub_rows:
            return None
        chosen = self._chosen_subfolder()
        if not chosen:
            return None
        return [chosen]

    def _rescan_current_folder(self) -> None:
        folder = self.cfg.get("folder", "")
        if folder and os.path.isdir(folder):
            self.load_folder(folder)

    def _apply_scan(self, token: int, tracks: list[Track]) -> None:
        if token != self._scan_token:
            return
        self.source = tracks
        self._manual_order = False
        self.rebuild_queue(keep_current=False)
        self.set_status(f"Loaded {len(tracks)} song{'s' if len(tracks) != 1 else ''} — "
                        f"sorted by {self.sort_mode}")
        self._start_metadata_worker(tracks)
        self._play_pending()

    def _play_pending(self) -> None:
        """Start the file that arrived from Explorer / a second launch."""
        extra, self._pending_extra = self._pending_extra, []
        if extra:
            self.add_files(extra)
        target, self._pending_play = self._pending_play, None
        if not target:
            return
        needle = os.path.normcase(os.path.normpath(target))
        for index, track in enumerate(self.queue):
            if os.path.normcase(os.path.normpath(track.path)) == needle:
                self.play_index(index)
                self.set_status(f"Playing «{track.display_title}» — opened from Explorer")
                return
        self.set_status("That file is not inside the loaded folder")

    def open_paths(self, paths: list[str]) -> None:
        """Second launch / double-click while the player is already running."""
        files = [p for p in paths if os.path.isfile(p)]
        folders = [p for p in paths if os.path.isdir(p)]
        if files:
            self._pending_play = os.path.abspath(files[0])
            self._pending_extra = [os.path.abspath(p) for p in files[1:]]
            self.load_folder(os.path.dirname(files[0]))
        elif folders:
            self.load_folder(folders[0])
        self.restore_window()

    def _poll_instance_mailbox(self) -> None:
        """About once a second: take over paths sent by another launch."""
        now = time.monotonic()
        if now - self._ipc_check < 1.0:
            return
        self._ipc_check = now
        try:
            request = single_instance.poll()
        except Exception:                                  # pragma: no cover
            return
        if request.get("paths"):
            self.open_paths(request["paths"])
        elif request.get("show"):
            self.restore_window()

    def _start_metadata_worker(self, tracks: list[Track]) -> None:
        if self._meta_running:
            return
        self._meta_running = True

        def worker():
            try:
                for track in tracks:
                    if self._closing:
                        break
                    if not track.meta_loaded:
                        library.read_metadata(track)
                        self._ui_queue.put(("meta", track))
            finally:
                self._meta_running = False
                self._ui_queue.put(("meta_done", None))

        threading.Thread(target=worker, daemon=True, name="tags").start()

    def sort_source(self) -> None:
        self.source = library.sort_tracks(self.source, self.sort_mode)
        self.rebuild_queue(keep_current=True)

    def rebuild_queue(self, keep_current: bool = True) -> None:
        current_path = self.current_track.path if (keep_current and self.current_track) else None
        tracks = saved.tracks() if self._saved_only else list(self.source)
        if self.sort_mode == "Manual (custom)" and self._manual_order:
            order = {t.path: i for i, t in enumerate(self.queue)}
            tracks.sort(key=lambda t: order.get(t.path, 1 << 30))
        else:
            tracks = library.sort_tracks(tracks, self.sort_mode)

        needle = self.search.text().strip().casefold()
        if needle:
            tracks = [t for t in tracks
                      if needle in t.display_title.casefold() or needle in t.name.casefold()
                      or needle in t.artist.casefold() or needle in t.album.casefold()]
        if self._liked_only:
            tracks = [t for t in tracks if t.path in self.liked]
        if self.shuffle:
            tracks = list(tracks)
            random.shuffle(tracks)

        self.queue = tracks
        self.playlist.set_tracks(tracks)
        if current_path:
            for index, track in enumerate(self.queue):
                if track.path == current_path:
                    self.current_index = index
                    self.playlist.set_current(index, self._playing)
                    break
            else:
                self.current_index = -1
                self.playlist.set_current(-1, False)
        elif not keep_current:
            self.current_index = -1
        self._update_counts()

    def _update_counts(self) -> None:
        total = len(self.source)
        shown = len(self.queue)
        text = f"{total} song{'s' if total != 1 else ''}"
        if self._liked_only:
            text += "   •   Liked Songs"
        if shown != total:
            text += f"   •   {shown} shown"
        minutes = sum(t.duration for t in self.source if t.duration) / 60.0
        if minutes:
            text += f"   •   {minutes:.0f} min"
        self.lbl_count.setText(text)
        self.chip_tracks.set_chip(f"{total} tracks")
        hero = getattr(self, "hero", None)
        if hero is not None:
            meta = f"{total} song{'s' if total != 1 else ''}"
            if minutes:
                meta += f"  •  {minutes:.0f} min"
            if shown != total:
                meta += f"  •  {shown} shown"
            hero.set_meta(meta)
            self._set_view_title()

    @staticmethod
    def _short_folder(folder: str) -> str:
        name = os.path.basename(folder.rstrip("\\/")) or folder
        return name if len(name) <= 28 else name[:26] + "…"

    def clear_playlist(self) -> None:
        if not self.source:
            return
        answer = QMessageBox.question(self, "Spoti Player",
                                      "Clear the playlist? (files are not deleted)")
        if answer != QMessageBox.Yes:
            return
        self.stop()
        self.source = []
        self.queue = []
        self.current_track = None
        self.playlist.set_tracks([])
        self.playlist.set_current(-1)
        self._update_counts()
        self.artwork.set_art(None, theme.VIOLET)
        self.lbl_title.setText("Nothing playing")
        self.lbl_artist.setText("—")
        self.lbl_album.setText("")
        self.lbl_tech.setText("")
        self._push_presence()
        self.set_status("Playlist cleared")

    def _on_search_changed(self, text: str) -> None:
        self.rebuild_queue(keep_current=True)
        query = (text or "").strip()
        self._online_token += 1
        if len(query) < 2:
            self._online_timer.stop()
            self._hide_online()
            return
        self._show_online_section(True)
        self.online_list.set_tracks([])
        self.online_list.set_message(f"Searching “{query}”…")
        self._online_timer.start()

    # ------------------------------------------------------------ online search #
    def _search_online(self) -> None:
        """Ask the catalogue in a worker thread (never block the UI)."""
        query = self.search.text().strip()
        if len(query) < 2:
            return
        token = self._online_token
        self._show_online_section(True)
        self.online_list.set_message(f"Searching “{query}”…")

        def worker():
            try:
                found = online.search(query)
            except Exception:
                self._ui_queue.put(("online_error", (token, query)))
                return
            self._ui_queue.put(("online", (token, query, found)))

        threading.Thread(target=worker, daemon=True, name="online-search").start()

    def _show_online(self, token: int, query: str, results: list) -> None:
        if token != self._online_token or query != self.search.text().strip():
            return                        # a newer search already replaced this one
        self.online_results = list(results)
        self._show_online_section(True)
        self.online_list.set_tracks(results)
        if not results:
            self.online_list.set_message(f"No online matches for “{query}”")
        index = -1
        if self.online_track is not None:
            for row, found in enumerate(results):
                if found.id == self.online_track.id:
                    index = row
                    break
        self._online_index = index
        self.online_list.set_current(index, self.online_track is not None and self._playing)
        self.set_status(
            f"{len(results)} online song{'s' if len(results) != 1 else ''} for “{query}”"
            if results else f"No online matches for “{query}”")

    def _show_online_section(self, visible: bool) -> None:
        """Open the search view: the big playlist hero steps aside for results."""
        self.online_card.setVisible(visible)
        hero = getattr(self, "hero", None)
        if hero is not None:
            hero.setVisible(not visible)

    def _hide_online(self) -> None:
        """Collapse the online section; the playing preview keeps its queue."""
        self._show_online_section(False)
        self.online_list.set_tracks([])

    # --------------------------------------------------------- online playback #
    def _leave_online(self) -> None:
        """Forget the preview when local playback or the radio takes over."""
        self.online_track = None
        self._online_index = -1
        self._online_art_request = None
        self._mini_cover_pixmap = None
        self.online_list.set_current(-1, False)
        self.online_list.set_playing(False)

    def _online_step(self, step: int) -> None:
        if not self.online_results:
            return
        index = 0 if self._online_index < 0 else \
            (self._online_index + step) % len(self.online_results)
        self.play_online(index)

    def play_online(self, index: int) -> None:
        """Play one 30-second preview from the current online results."""
        if not (0 <= index < len(self.online_results)):
            return
        track = self.online_results[index]
        if not track.preview_url:
            self.set_status("This result has no preview to play")
            return
        if self.radio_mode:
            self._stop_radio(announce=False)
        self.engine.stop()
        self._online_art_request = None
        self.online_track = track
        self._online_index = index
        self.current_track = None                 # no local row is playing
        self.current_index = -1
        self._end_handled = False
        if not self.engine.play_path(track.preview_url):
            self.online_track = None
            self._online_index = -1
            self.online_list.set_current(-1, False)
            self.online_list.set_playing(False)
            self.set_status("Could not play this preview — check your connection")
            return
        self._playing = True
        self._refresh_play_icon()
        self.playlist.set_playing(False)
        self.playlist.set_current(-1, False)
        self.online_list.set_current(index, True)
        self.online_list.set_playing(True)
        self.lbl_title.setText(track.title)
        self.lbl_artist.setText(track.artist)
        self.lbl_album.setText(track.album)
        self.lbl_tech.setText(track.tech_line())
        self.lbl_total.setText("0:30")
        self.lbl_now.setText("0:00")
        self.seek.set_position(0.0)
        self.bar_title.setText(track.title)
        self.bar_artist.setText(track.artist)
        self._sync_like_ui()
        self._request_online_art(track)
        self._push_presence()
        self._sync_mini_bar()
        self.set_status(f"Preview: {track.title} — {track.artist}")

    def _request_online_art(self, track: online.OnlineTrack) -> None:
        """Fetch the cover in the background (cached on disk by URL)."""
        self._online_art_request = track.id
        self._art_request = None
        self.artwork.set_art(None, theme.GREEN)
        self.artwork.set_playing(True)
        self.bar_cover.set_art(None, theme.GREEN)
        self.playlist.set_tint(theme.color(theme.GREEN))

        def worker():
            try:
                image = online.fetch_artwork(track, config.cache_dir(), ART_SIZE)
            except Exception:
                image = self.art.placeholder(track.display_title, (ART_SIZE, ART_SIZE))
            try:
                accent = theme.dominant_color(image)
            except Exception:
                accent = theme.GREEN
            self._ui_queue.put(("online_art", (track.id, image, accent)))

        threading.Thread(target=worker, daemon=True, name="online-art").start()

    def _online_menu(self, position: QPoint, index: int) -> None:
        if not (0 <= index < len(self.online_results)):
            return
        track = self.online_results[index]
        entry = saved.find(track.id)
        menu = QMenu(self)
        info = menu.addAction(f"{track.artist} — {track.album}" if track.album
                              else track.artist)
        info.setEnabled(False)
        menu.addSeparator()
        play = menu.addAction("Play the 30-second preview")
        play.triggered.connect(lambda: self.play_online(index))
        if entry:
            open_saved = menu.addAction("Play the downloaded song (offline)")
            open_saved.triggered.connect(lambda: self.play_saved(entry["file"]))
            delete = menu.addAction("Delete the downloaded song from this PC")
            delete.triggered.connect(lambda: self.remove_saved(entry["file"]))
        else:
            fraction = self._saving.get(track.id)
            label = (f"Downloading — {int(fraction * 100)}%" if fraction is not None
                     else "Download the full song")
            save = menu.addAction(label)
            save.setEnabled(fraction is None)
            save.triggered.connect(lambda: self.download_online(index))
        menu.addSeparator()
        full = menu.addAction("Open the full song in Apple Music")
        full.setEnabled(bool(track.page_url))
        full.triggered.connect(lambda: webbrowser.open(track.page_url))
        copy_link = menu.addAction("Copy the Apple Music link")
        copy_link.setEnabled(bool(track.page_url))
        copy_link.triggered.connect(lambda: QApplication.clipboard().setText(track.page_url))
        if saved.entries():
            menu.addSeparator()
            folder = menu.addAction("Open the downloads folder")
            folder.triggered.connect(self.open_downloads_folder)
        menu.exec(position)
        menu.deleteLater()

    # ------------------------------------------------ downloading for offline #
    def download_online(self, index: int) -> None:
        """Download the whole song for the result at *index* and keep it here."""
        if not (0 <= index < len(self.online_results)):
            return
        track = self.online_results[index]
        if saved.find(track.id):
            self.set_status(f"“{track.title}” is already downloaded")
            return
        if track.id in self._saving:
            self.set_status(f"“{track.title}” is already being downloaded")
            return
        self._saving[track.id] = 0.0
        self.online_list.set_saving(track.id, 0.0)
        self.set_status(f"Finding the full song “{track.title}” …")
        target = saved.folder()

        def worker():
            last = [-1.0]

            def progress(done: int, total: int) -> None:
                fraction = (done / total) if total else 0.0
                if fraction - last[0] >= 0.04 or fraction >= 1.0:
                    last[0] = fraction
                    self._ui_queue.put(("saving", (track.id, fraction)))

            try:
                image = online.fetch_artwork(track, config.cache_dir(), ART_SIZE)
            except Exception:
                image = None
            try:
                path, duration = fulltrack.download(track, target, progress)
            except Exception as exc:
                self._ui_queue.put(("saved_error", (track.id, str(exc))))
                return
            saved.write_tags(path, track, image)
            saved.add(track, path, duration)
            self._ui_queue.put(("saved_done", (track.id, str(path))))

        threading.Thread(target=worker, daemon=True, name="online-download").start()

    def play_saved(self, path: str) -> None:
        """Play one of the songs downloaded from the online search."""
        self.show_downloads(True)
        needle = os.path.normcase(os.path.normpath(path))
        for index, track in enumerate(self.queue):
            if os.path.normcase(os.path.normpath(track.path)) == needle:
                self.play_index(index)
                return
        self.set_status("That downloaded song is not on disk any more")

    def open_downloads_folder(self) -> None:
        """Open the folder that keeps the downloaded songs."""
        try:
            subprocess.Popen(["explorer", str(saved.folder())])
        except Exception:
            self.set_status(f"Downloads live in {saved.folder()}")

    def remove_saved(self, path: str, confirm: bool = True) -> None:
        """Delete a downloaded song (and its index entry) from this PC."""
        name = os.path.basename(path)
        if confirm:
            answer = QMessageBox.question(
                self, "Delete the downloaded song",
                f"Delete “{name}” from this PC?\n\nThe song in the online catalogue stays untouched.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        if self.current_track is not None and \
                os.path.normcase(self.current_track.path) == os.path.normcase(path):
            self.stop()
        saved.remove(path)
        self.saved = saved.tracks()
        self._saved_ids = saved.ids()
        self.online_list.set_saved(self._saved_ids)
        self._sync_saved_row()
        if self._saved_only:
            self.rebuild_queue(keep_current=True)
        self.set_status("Deleted the downloaded song from this PC")

    def _on_sort_changed(self, mode: str) -> None:
        if not mode:
            return
        self.sort_mode = mode
        self.cfg["sort_mode"] = mode
        self._manual_order = False
        config.save(self.cfg)
        self.rebuild_queue(keep_current=True)
        self.set_status(f"Sorted by {mode}")

    # --------------------------------------------------------------- reorder #
    def move_selected(self, delta: int) -> None:
        row = self.playlist.currentRow()
        if not (0 <= row < len(self.queue)):
            self.set_status("Select a song in the list first")
            return
        target = row + delta
        if not (0 <= target < len(self.queue)):
            return
        self.queue[row], self.queue[target] = self.queue[target], self.queue[row]
        if self.current_track:
            for index, track in enumerate(self.queue):
                if track.path == self.current_track.path:
                    self.current_index = index
                    break
        self._manual_order = True
        self.sort_mode = "Manual (custom)"
        self.cfg["sort_mode"] = self.sort_mode
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentText(self.sort_mode)
        self.sort_combo.blockSignals(False)
        self.playlist.set_tracks(self.queue)
        self.playlist.setCurrentRow(target)
        self.playlist.set_current(self.current_index, self._playing)
        self.playlist.scroll_to_current()
        self.set_status("Custom order — pick a sort option to restore automatic sorting")

    def remove_selected(self) -> None:
        row = self.playlist.currentRow()
        if not (0 <= row < len(self.queue)):
            return
        track = self.queue[row]
        if self.current_track and track.path == self.current_track.path:
            self.next_track(1)
        self.queue.pop(row)
        self.source = [t for t in self.source if t.path != track.path]
        self._manual_order = True
        if self.current_track:
            for index, item in enumerate(self.queue):
                if item.path == self.current_track.path:
                    self.current_index = index
                    break
        self.playlist.set_tracks(self.queue)
        self.playlist.setCurrentRow(min(row, len(self.queue) - 1))
        self.playlist.set_current(self.current_index, self._playing)
        self._update_counts()
        self.set_status(f"Removed “{track.display_title}” from the playlist")

    def _playlist_menu(self, position: QPoint, row: int) -> None:
        if not (0 <= row < len(self.queue)):
            return
        track = self.queue[row]
        menu = QMenu(self)
        play = QAction("▶   Play", self)
        play.triggered.connect(lambda: self.play_index(row))
        up = QAction("↑   Move up", self)
        up.triggered.connect(lambda: self.move_selected(-1))
        down = QAction("↓   Move down", self)
        down.triggered.connect(lambda: self.move_selected(1))
        reveal = QAction("Show in Explorer", self)
        reveal.triggered.connect(lambda: self.reveal_path(track.path))
        remove = QAction("Remove from list", self)
        remove.triggered.connect(self.remove_selected)
        saved_files = {os.path.normcase(item["file"]) for item in saved.entries()}
        is_saved = os.path.normcase(track.path) in saved_files
        liked = track.path in self.liked
        like = QAction("◦   Remove from Liked Songs" if liked else "♥   Add to Liked Songs", self)
        like.triggered.connect(lambda: self.toggle_like(track))
        menu.addAction(play)
        menu.addAction(like)
        menu.addSeparator()
        menu.addAction(up)
        menu.addAction(down)
        menu.addSeparator()
        menu.addAction(reveal)
        menu.addAction(remove)
        if is_saved:
            delete = QAction("Delete the downloaded song from this PC", self)
            delete.triggered.connect(lambda: self.remove_saved(track.path))
            menu.addAction(delete)
        menu.exec(position)

    # ===================================================================== #
    #  playback
    # ===================================================================== #
    def play_index(self, index: int) -> None:
        if not (0 <= index < len(self.queue)):
            return
        if self.radio_mode:
            self._stop_radio(announce=False)
        self._leave_online()
        track = self.queue[index]
        self.current_index = index
        self.current_track = track
        self._end_handled = False
        if self.engine.play_path(track.path):
            self._playing = True
        self._refresh_play_icon()
        self.playlist.set_current(index, True)
        self.playlist.scroll_to_current()
        self._flash_current_row()
        self._update_now_playing(track)
        self._push_presence()
        self.set_status(f"Playing   {track.display_title}")

    def toggle_play(self) -> None:
        if self.radio_mode:
            if self.engine.is_playing():
                self.engine.pause()
                self._playing = False
            else:
                self.engine.set_pause(False)
                self._playing = True
            self._refresh_play_icon()
            self.artwork.set_playing(self._playing)
            self._push_presence()
            self._sync_mini_bar()
            return
        if self.online_track is not None:
            if self.engine.is_playing():
                self.engine.pause()
                self._playing = False
            elif self.engine.state().endswith("Ended"):
                self._online_step(1)
                return
            else:
                self.engine.set_pause(False)
                self._playing = True
            self._refresh_play_icon()
            self.artwork.set_playing(self._playing)
            self.online_list.set_playing(self._playing)
            self._push_presence()
            self._sync_mini_bar()
            return
        if self.current_track is None:
            if self.queue:
                self.play_index(0)
            return
        if self.engine.is_playing():
            self.engine.pause()
            self._playing = False
        else:
            if self.engine.state().endswith("Ended") or self.engine.state().endswith("Stopped"):
                self.engine.play_path(self.current_track.path)
            else:
                self.engine.set_pause(False)
            self._playing = True
        self._refresh_play_icon()
        self.playlist.set_playing(self._playing)
        self.artwork.set_playing(self._playing)
        self._push_presence()
        self._sync_mini_bar()

    def stop(self) -> None:
        if self.radio_mode:
            self._stop_radio(announce=True)
            return
        self._leave_online()
        self.engine.stop()
        self._playing = False
        self._refresh_play_icon()
        self.playlist.set_playing(False)
        self.artwork.set_playing(False)
        self.seek.set_position(0.0)
        self.lbl_now.setText("0:00")
        self._push_presence()
        self._sync_mini_bar()
        self.set_status("Stopped")

    def next_track(self, step: int = 1) -> None:
        if self.radio_mode:
            self._radio_next(step)
            return
        if self.online_track is not None:
            self._online_step(step)
            return
        if not self.queue:
            return
        if self.current_index < 0:
            self.play_index(0)
            return
        index = self.current_index + step
        if index >= len(self.queue):
            if self.repeat == "all":
                index = 0
            else:
                self.stop()
                self.set_status("End of playlist")
                return
        if index < 0:
            index = len(self.queue) - 1 if self.repeat == "all" else 0
        self.play_index(index)

    def _on_track_ended(self) -> None:
        if self.radio_mode:
            self._radio_next(1)
            return
        if self.online_track is not None:
            if self._online_index + 1 < len(self.online_results):
                self.play_online(self._online_index + 1)
            else:
                self.stop()
                self.set_status("End of the online previews")
            return
        if self.repeat == "one" and self.current_track:
            self.play_index(self.current_index)
            return
        last = self.current_index >= len(self.queue) - 1
        if last and self.repeat == "off":
            self._playing = False
            self._refresh_play_icon()
            self.playlist.set_playing(False)
            self.artwork.set_playing(False)
            self._push_presence()
            self.set_status("Playlist finished")
            return
        if last and self.repeat == "all" and self.queue:
            self.play_index(0)
            return
        self.next_track(1)

    def _handle_ended(self) -> None:
        if self._end_handled:
            return
        self._end_handled = True
        self._on_track_ended()

    def cycle_repeat(self) -> None:
        order = ["off", "all", "one"]
        self.repeat = order[(order.index(self.repeat) + 1) % len(order)] if self.repeat in order else "all"
        self.cfg["repeat"] = self.repeat
        config.save(self.cfg)
        self._refresh_repeat_icon()
        self.set_status({"off": "Repeat: off", "all": "Repeat: all songs",
                         "one": "Repeat: this song"}[self.repeat])

    def toggle_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        self.cfg["shuffle"] = self.shuffle
        config.save(self.cfg)
        self.btn_shuffle.setChecked(self.shuffle)
        hero = getattr(self, "hero", None)
        if hero is not None:
            hero.shuffle.setChecked(self.shuffle)
        self.rebuild_queue(keep_current=True)
        self.playlist.scroll_to_current()
        self.set_status("Shuffle on — queue reordered" if self.shuffle else "Shuffle off — original order")

    def _refresh_play_icon(self) -> None:
        self.btn_play.set_kind("pause" if self._playing else "play")
        self.btn_play.set_accent(self._tint if self._playing else theme.CYAN)
        hero = getattr(self, "hero", None)
        if hero is not None:
            hero.play.set_kind("pause" if self._playing else "play")

    def _refresh_repeat_icon(self) -> None:
        self.btn_repeat.set_kind("repeat1" if self.repeat == "one" else "repeat")
        self.btn_repeat.setChecked(self.repeat != "off")

    def _nudge(self, delta_ms: int) -> None:
        if self.current_track is None and self.online_track is None:
            return
        self.engine.seek_ms(max(0, self.engine.time_ms() + delta_ms))
        self.set_status(f"Seek {delta_ms / 1000:+.0f}s")

    def _seek_preview(self, fraction: float) -> None:
        length = self.engine.length_ms() or int((self.current_track.duration if self.current_track else 0) * 1000)
        if length > 0:
            self.lbl_now.setText(format_time(fraction * length / 1000.0))

    def _seek_committed(self, fraction: float) -> None:
        self._seeking = False
        length = self.engine.length_ms() or int((self.current_track.duration if self.current_track else 0) * 1000)
        if length > 0:
            self.engine.seek_ms(int(fraction * length))
        else:
            self.engine.seek_fraction(fraction)
        self._push_presence()

    # ===================================================================== #
    #  volume / device
    # ===================================================================== #
    def _volume_changed(self, volume: int) -> None:
        self.engine.set_volume(int(volume))
        self._sync_volume_ui(volume)

    def _nudge_volume(self, delta: int) -> None:
        self.engine.set_volume(self.engine.volume + delta)
        self._sync_volume_ui()

    def toggle_mute(self) -> None:
        self.engine.set_muted()
        self._sync_volume_ui()
        self.set_status("Muted" if self.engine.muted else f"Volume {self.engine.volume}%")

    def _sync_volume_ui(self, volume: int | None = None) -> None:
        volume = self.engine.volume if volume is None else volume
        muted = self.engine.muted
        self.volume.set_volume(volume, engine_mod.VOLUME_MAX)
        if muted:
            text, color = "muted", theme.TEXT_MUTED
        elif volume > 100:
            text, color = f"{volume} %  boost", theme.ORANGE
        else:
            text, color = f"{volume} %", theme.EMERALD
        self.lbl_volume.setText(text)
        self.lbl_volume.setStyleSheet(f"color: {theme.hexstr(color)};")
        self.btn_mute.set_kind("mute" if muted else "volume")
        self.btn_mute.setChecked(muted)
        self.cfg["volume"] = volume
        self.cfg["muted"] = muted

    def _refresh_devices(self, select_saved: bool = False) -> None:
        self.engine.invalidate_devices()
        pairs = self.engine.output_devices(max_age=0.0)
        self._device_pairs = pairs
        names = [name for name, _ in pairs]
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItems(names)
        wanted = self.cfg.get("device_id", "")
        chosen = None
        if select_saved and wanted:
            for name, dev_id in pairs:
                if dev_id == wanted:
                    chosen = name
                    self.engine.set_output_device(dev_id, name)
                    break
        if chosen is None:
            chosen = names[0] if names else "Default"
            dev_id = dict(pairs).get(chosen, "")
            self.engine.set_output_device(dev_id, chosen)
        self.device_combo.setCurrentText(chosen)
        self.device_combo.blockSignals(False)
        self.chip_device.set_chip(f"out: {chosen[:26]}")
        if select_saved:
            self.set_status(f"Audio output: {chosen}")

    def _on_device_selected(self, name: str) -> None:
        if not name:
            return
        dev_id = dict(self._device_pairs).get(name, "")
        self.engine.set_output_device(dev_id, name)
        self.cfg["device_id"] = dev_id
        self.cfg["device_name"] = name
        config.save(self.cfg)
        self.chip_device.set_chip(f"out: {name[:26]}")
        self.set_status(f"Audio output switched to {name}")


    # ===================================================================== #
    #  liked songs
    # ===================================================================== #
    def toggle_like(self, track: Track | None = None) -> None:
        """Add/remove a song from Liked Songs (the ♥ button, or the right-click menu)."""
        track = track or self.current_track
        if track is None:
            self.set_status("Play a song first — then press ♥ to like it")
            return
        if track.path in self.liked:
            self.liked.discard(track.path)
            message = f"Removed «{track.display_title}» from Liked Songs"
        else:
            self.liked.add(track.path)
            message = f"Added «{track.display_title}» to Liked Songs"
        self.cfg["liked"] = sorted(self.liked)
        config.save(self.cfg)
        self.playlist.set_liked(self.liked)
        self._sync_like_ui()
        self._sync_liked_row()
        if self._liked_only:
            self.rebuild_queue(keep_current=True)
        self.set_status(message)

    def _sync_like_ui(self) -> None:
        track = self.current_track
        liked = bool(track and track.path in self.liked)
        for button in (self.btn_like, getattr(self, "btn_np_like", None)):
            if button is None:
                continue
            button.set_kind("heart_filled" if liked else "heart")
            button.set_accent(theme.GREEN if liked else theme.TEXT_DIM)
            button.setEnabled(track is not None)

    def _sync_liked_row(self) -> None:
        self.liked_row.set_state(len(self.liked), self._liked_only)

    def show_liked_songs(self, active: bool | None = None) -> None:
        """Show only the hearted songs — Spotify's Liked Songs playlist."""
        self._liked_only = (not self._liked_only) if active is None else bool(active)
        self._saved_only = False
        self._sync_liked_row()
        self._sync_saved_row()
        self._set_view_title()
        self.rebuild_queue(keep_current=True)
        self._remember_state()
        count = len(self.liked)
        self.set_status(f"Liked Songs — {count} song{'s' if count != 1 else ''}"
                        if self._liked_only else "Back to the whole playlist")

    def _sync_saved_row(self) -> None:
        row = getattr(self, "saved_row", None)
        if row is not None:
            row.set_state(len(self.saved), self._saved_only)

    def show_downloads(self, active: bool | None = None) -> None:
        """Show the songs downloaded on this PC — a playlist that works offline."""
        self._saved_only = (not self._saved_only) if active is None else bool(active)
        self._liked_only = False
        self.saved = saved.tracks()
        self._sync_saved_row()
        self._sync_liked_row()
        self._set_view_title()
        self.rebuild_queue(keep_current=True)
        self._remember_state()
        count = len(self.saved)
        self.set_status(f"Downloads — {count} song{'s' if count != 1 else ''} "
                        f"({saved.total_bytes() / (1024 * 1024):.1f} MB)"
                        if self._saved_only else "Back to the whole playlist")

    def _view_name(self) -> str:
        """The name of the view the window is showing (folder / sub-folder / liked)."""
        if self._saved_only:
            return "Downloads"
        if self._liked_only:
            return "Liked Songs"
        choice = self.cfg.get("subfolder_choice") or ""
        if choice and choice != "All songs":
            return choice
        folder = self.cfg.get("folder") or ""
        return os.path.basename(folder.rstrip("\\/")) or "All songs"

    def _set_view_title(self) -> None:
        hero = getattr(self, "hero", None)
        if hero is None:
            return
        title = self._view_name()
        if self._saved_only:
            glyph = "download"
        elif self._liked_only:
            glyph = "heart"
        else:
            glyph = "note"
        hero.set_view(title, eyebrow="Playlist", seed=title, glyph=glyph)

    # ===================================================================== #
    #  history (back / forward / home of the top bar)
    # ===================================================================== #
    def _current_state(self) -> dict:
        return {"folder": self.cfg.get("folder", ""),
                "choice": self.cfg.get("subfolder_choice") or "All songs",
                "liked": self._liked_only,
                "saved": self._saved_only}

    def _remember_state(self) -> None:
        """Push the current view onto the back/forward history (deduplicated)."""
        state = self._current_state()
        if 0 <= self._history_index < len(self._history) \
                and self._history[self._history_index] == state:
            self._sync_nav_buttons()
            return
        del self._history[self._history_index + 1:]
        self._history.append(state)
        self._history_index = len(self._history) - 1
        self._sync_nav_buttons()

    def _sync_nav_buttons(self) -> None:
        self.btn_back.setEnabled(self._history_index > 0)
        self.btn_forward.setEnabled(0 <= self._history_index < len(self._history) - 1)

    def _apply_state(self, state: dict) -> None:
        self._liked_only = bool(state.get("liked"))
        self._saved_only = bool(state.get("saved"))
        folder = state.get("folder") or ""
        self.cfg["subfolder_choice"] = state.get("choice") or "All songs"
        self.cfg["subfolders_for"] = folder
        self._sync_liked_row()
        self._sync_saved_row()
        self._set_view_title()
        if folder and os.path.isdir(folder):
            self.cfg["folder"] = folder          # so the remembered choice is kept
            self.load_folder(folder)
        else:
            self.rebuild_queue(keep_current=True)
        self._sync_nav_buttons()

    def go_back(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self._apply_state(self._history[self._history_index])

    def go_forward(self) -> None:
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._apply_state(self._history[self._history_index])

    def go_home(self) -> None:
        """The Home button: the whole folder, no filter, no liked-only view."""
        if self.search.text():
            self.search.clear()
        self._liked_only = False
        self._saved_only = False
        self.cfg["subfolder_choice"] = "All songs"
        self.cfg["subfolders_for"] = self.cfg.get("folder", "")
        self._sync_liked_row()
        self._sync_saved_row()
        self._set_view_title()
        self.rebuild_queue(keep_current=True)
        self._remember_state()
        self.set_status("Home — the whole playlist")

    # ===================================================================== #
    #  artwork + now playing
    # ===================================================================== #
    def _update_now_playing(self, track: Track | None) -> None:
        if track is None:
            return
        self.lbl_title.setText(track.display_title)
        self.lbl_artist.setText(track.display_artist)
        self.bar_title.setText(track.display_title)
        self.bar_artist.setText(track.display_artist)
        self.lbl_album.setText(track.album or track.name)
        self.lbl_tech.setText(track.tech_line() or track.name)
        self.lbl_total.setText(format_time(track.duration))
        self._sync_like_ui()
        self._request_art(track)
        self._sync_mini_bar()

    def _request_art(self, track: Track | None) -> None:
        self._art_request = track.path if track else None
        if track is None:
            self.artwork.set_art(None, theme.VIOLET)
            self.bar_cover.set_art(None, theme.GREEN)
            return

        def worker():
            try:
                image = self.art.image(track, (ART_SIZE, ART_SIZE))
            except Exception:
                image = self.art.placeholder(track.display_title, (ART_SIZE, ART_SIZE))
            try:
                accent = theme.dominant_color(image)
            except Exception:
                accent = theme.CYAN
            self._ui_queue.put(("art", (track.path, image, accent)))

        threading.Thread(target=worker, daemon=True, name="art").start()

    def _apply_art(self, image, accent: str) -> None:
        pixmap = pil_to_pixmap(image)
        self.artwork.set_art(pixmap, accent)
        self.artwork.set_playing(self._playing)
        self.bar_cover.set_art(pixmap, accent)
        self.playlist.set_tint(theme.color(accent))
        self._mini_cover_pixmap = pixmap.scaled(96, 96, Qt.KeepAspectRatioByExpanding,
                                                Qt.SmoothTransformation)
        self._animate_song_change(accent)
        self._sync_mini_bar()

    def reveal_current(self) -> None:
        if self.current_track:
            self.reveal_path(self.current_track.path)

    @staticmethod
    def reveal_path(path: str) -> None:
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass

    # ===================================================================== #
    #  discord rich presence
    # ===================================================================== #
    def toggle_discord(self) -> None:
        if self.presence.enabled:
            self.presence.set_enabled(False)
            self.cfg["discord_enabled"] = False
            config.save(self.cfg)
            self._push_presence()
            self.set_status("Discord Rich Presence off")
            return
        self.presence.set_enabled(True)
        self.cfg["discord_enabled"] = True
        config.save(self.cfg)
        self._push_presence()
        self.set_status("Discord Rich Presence on")

    def discord_setup(self) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Discord Rich Presence — setup")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        head = SectionHeader("Discord Application ID", theme.DISCORD)
        layout.addWidget(head)
        info = QLabel(discord_rpc.describe_setup(self.presence.client_id))
        info.setObjectName("Hint")
        info.setWordWrap(True)
        layout.addWidget(info)

        id_edit = QLineEdit(self.presence.client_id)
        id_edit.setPlaceholderText("17–20 digit Application ID")
        id_edit.setFont(theme.mono(11))
        layout.addWidget(id_edit)

        asset_label = QLabel("RICH PRESENCE IMAGE KEY (optional, default “spoti”)")
        asset_label.setObjectName("SectionTitle")
        layout.addWidget(asset_label)
        asset_edit = QLineEdit(self.presence.large_image or "spoti")
        asset_edit.setFont(theme.mono(10))
        layout.addWidget(asset_edit)

        buttons = QHBoxLayout()
        portal = PillButton("Open developer portal", theme.GREEN, "outline", height=32, font_size=8)
        portal.clicked.connect(lambda: webbrowser.open("https://discord.com/developers/applications"))
        cancel = PillButton("Cancel", theme.TEXT_MUTED, "ghost", height=32, font_size=8)
        cancel.clicked.connect(dialog.reject)
        save = PillButton("Save", theme.DISCORD, "solid", height=32, font_size=8)
        buttons.addWidget(portal)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        result = {"ok": False}

        def do_save():
            client_id = id_edit.text().strip()
            if client_id and not client_id.isdigit():
                QMessageBox.warning(dialog, "Spoti Player",
                                    "A Discord Application ID is a long number (17–20 digits).")
                return
            asset = asset_edit.text().strip() or "spoti"
            self.presence.set_large_image(asset)
            self.presence.set_client_id(client_id)
            self.cfg["discord_client_id"] = client_id
            self.cfg["discord_large_image"] = asset
            if client_id and not self.presence.enabled:
                self.presence.set_enabled(True)
                self.cfg["discord_enabled"] = True
            config.save(self.cfg)
            result["ok"] = True
            dialog.accept()

        save.clicked.connect(do_save)
        id_edit.returnPressed.connect(do_save)
        id_edit.setFocus()
        dialog.exec()
        return result["ok"]

    # ===================================================================== #
    #  settings — Discord · default player · startup
    # ===================================================================== #
    def open_settings(self) -> bool:
        """One dialog for everything Windows-ish: Discord, default player,
        run at startup. Every change is written immediately."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Spoti Player — Settings")
        dialog.setMinimumWidth(620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        # ---- Discord Rich Presence ------------------------------------- #
        layout.addWidget(SectionHeader("Discord Rich Presence", theme.DISCORD))
        discord_row = QHBoxLayout()
        discord_row.setSpacing(8)
        lbl_discord = QLabel(self.presence.status_text() if self.presence.enabled else "Off")
        lbl_discord.setObjectName("Hint")
        discord_row.addWidget(lbl_discord, 1)
        btn_discord_on = PillButton("ON" if self.presence.enabled else "OFF", theme.DISCORD,
                                    "ghost", height=26, font_size=8,
                                    tooltip="Show what you are listening to on Discord")
        discord_row.addWidget(btn_discord_on)
        layout.addLayout(discord_row)

        def refresh_discord():
            enabled = self.presence.enabled
            btn_discord_on.setText("ON" if enabled else "OFF")
            btn_discord_on.set_variant("solid" if enabled else "ghost")
            lbl_discord.setText(self.presence.status_text() if enabled else "Off")

        btn_discord_on.clicked.connect(lambda: (self.toggle_discord(), refresh_discord()))

        # ---- default music player (Windows) ---------------------------- #
        layout.addWidget(SectionHeader("Default music player (Windows)", theme.GREEN))
        default_info = QLabel(
            "Double-click a song in Explorer → Spoti Player opens and plays it. "
            "Windows stores this choice per user, so it may ask you to confirm it once "
            "in the «Default apps» window that opens.")
        default_info.setObjectName("Hint")
        default_info.setWordWrap(True)
        layout.addWidget(default_info)

        default_row = QHBoxLayout()
        default_row.setSpacing(8)
        lbl_default = QLabel("")
        lbl_default.setObjectName("Hint")
        default_row.addWidget(lbl_default, 1)
        btn_win_apps = PillButton("Windows settings", theme.TEXT_MUTED, "outline",
                                  height=26, font_size=8,
                                  tooltip="Open Windows «Default apps»")
        btn_win_apps.clicked.connect(winint.open_windows_default_apps)
        btn_default = PillButton("OFF", theme.GREEN, "ghost", height=26, font_size=8,
                                 tooltip="Hand mp3/wav/flac/… to Spoti Player")
        btn_default.setCheckable(True)
        default_row.addWidget(btn_win_apps)
        default_row.addWidget(btn_default)
        layout.addLayout(default_row)

        def refresh_default():
            state = winint.is_registered()
            btn_default.setChecked(state)
            btn_default.setText("ON" if state else "OFF")
            btn_default.set_variant("solid" if state else "ghost")
            btn_default.set_accent(theme.GREEN if state else theme.TEXT_MUTED)
            lbl_default.setText("Registered — double-click any song to play it here"
                                if state else "Not registered — double-clicks are not ours yet")

        def on_default(checked: bool) -> None:
            if checked:
                ok = winint.register()
                if ok:
                    winint.open_windows_default_apps()
                self.cfg["default_player"] = winint.is_registered()
                config.save(self.cfg)
                self.set_status("Spoti Player registered as an audio player — confirm it "
                                "in the Windows window that opened"
                                if ok else "Could not write the file associations (HKCU)")
            else:
                winint.unregister()
                self.cfg["default_player"] = False
                config.save(self.cfg)
                self.set_status("File associations removed — other players are back")
            refresh_default()

        btn_default.clicked.connect(on_default)
        refresh_default()

        # ---- run at startup -------------------------------------------- #
        layout.addWidget(SectionHeader("STARTUP (WINDOWS)", theme.GREEN))
        startup_info = QLabel("When Windows starts, Spoti Player comes up by itself — "
                              "minimised in the system tray, with no window.")
        startup_info.setObjectName("Hint")
        startup_info.setWordWrap(True)
        layout.addWidget(startup_info)

        startup_row = QHBoxLayout()
        startup_row.setSpacing(8)
        lbl_startup = QLabel("")
        lbl_startup.setObjectName("Hint")
        startup_row.addWidget(lbl_startup, 1)
        chk_startup = QCheckBox("Open minimised in the tray when Windows starts")
        startup_row.addWidget(chk_startup)
        layout.addLayout(startup_row)

        def refresh_startup(enabled: bool) -> None:
            lbl_startup.setText("On — starts with Windows, hidden in the tray"
                                if enabled else "Off — only starts when you open it")

        chk_startup.setChecked(winint.startup_enabled())
        refresh_startup(chk_startup.isChecked())

        def on_startup(checked: bool) -> None:
            ok = winint.set_startup_enabled(bool(checked))
            self.cfg["startup"] = bool(checked) and ok
            config.save(self.cfg)
            chk_startup.blockSignals(True)
            chk_startup.setChecked(bool(ok) and bool(checked))
            chk_startup.blockSignals(False)
            refresh_startup(bool(ok) and bool(checked))
            if ok:
                self.set_status("Spoti Player will start with Windows (minimised)"
                                if checked else "Spoti Player removed from Windows startup")
            else:
                self.set_status("Windows refused the startup entry — startup is locked "
                                "down by policy on this account")

        chk_startup.toggled.connect(on_startup)

        # ---- close ------------------------------------------------------ #
        buttons = QHBoxLayout()
        saved = QLabel("Changes are saved immediately.")
        saved.setObjectName("Hint")
        buttons.addWidget(saved)
        buttons.addStretch(1)
        close = PillButton("Close", theme.GREEN, "solid", height=32, font_size=9)
        close.clicked.connect(dialog.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        dialog.exec()
        return False

    def _push_presence(self, force: bool = False) -> None:
        if not self.presence.enabled and not force:
            return
        if self.radio_mode:
            station = self._radio_station_obj()
            if station is None:
                self.presence.update_track(None)
                return
            title = self._radio_title()
            track = type("RadioTrack", (), {
                "display_title": title, "name": title,
                "artist": station.name if station.has_songs else station.blurb,
                "album": "", "duration": 0,
            })()
            length = self.engine.length_ms() or 0
            self.presence.update_track(track, position_ms=self.engine.time_ms(),
                                       duration_ms=length, paused=not self._playing)
            return
        if self.online_track is not None:
            self.presence.update_track(self.online_track,
                                       position_ms=self.engine.time_ms(),
                                       duration_ms=int(online.PREVIEW_SECONDS * 1000),
                                       paused=not self._playing)
            return
        if self.current_track is None:
            self.presence.update_track(None)
            return
        length = self.engine.length_ms() or int((self.current_track.duration or 0) * 1000)
        self.presence.update_track(self.current_track, position_ms=self.engine.time_ms(),
                                   duration_ms=length, paused=not self._playing)


    # ===================================================================== #
    #  mini bar (always-on-top overlay)
    # ===================================================================== #
    def _ensure_mini_bar(self) -> MiniBar:
        if self.mini_bar is None:
            bar = MiniBar()
            bar.playPauseRequested.connect(self.toggle_play)
            bar.nextRequested.connect(lambda: self.next_track(1))
            bar.previousRequested.connect(lambda: self.next_track(-1))
            bar.closeRequested.connect(self.hide_mini_bar)
            bar.stopRequested.connect(self.stop)
            bar.restoreRequested.connect(self.restore_window)
            bar.topmostBlocked.connect(self._on_topmost_blocked)
            bar.set_performance(self.performance_mode)
            self.mini_bar = bar
        return self.mini_bar

    def _on_topmost_blocked(self) -> None:
        """Something owns the screen (exclusive fullscreen) - say so, briefly."""
        self.set_status("A fullscreen game is covering the mini bar — "
                        "games must run in Borderless / Windowed fullscreen for it to show")
        if getattr(self, "tray", None) is not None and self.tray.isVisible():
            try:
                self.tray.showMessage(
                    "Mini bar is hidden by a fullscreen game",
                    "Switch the game to Borderless / Windowed fullscreen so the bar can be shown.",
                    QSystemTrayIcon.Warning, 8000)
            except Exception:
                pass

    def toggle_mini_bar(self) -> None:
        if self.mini_bar is not None and self.mini_bar.isVisible():
            self.hide_mini_bar()
        else:
            self.show_mini_bar()

    def show_mini_bar(self) -> None:
        bar = self._ensure_mini_bar()
        self._sync_mini_bar(force=True)
        bar.place_top_centre()
        bar.show()
        bar.raise_()
        bar._assert_topmost()
        self.cfg["mini_bar"] = True
        config.save(self.cfg)
        self.btn_mini.setChecked(True)
        self.action_mini.setChecked(True)
        self.set_status("Mini bar is on top — it stays above other apps until you close it")

    def hide_mini_bar(self) -> None:
        if self.mini_bar is not None:
            self.mini_bar.hide()
        self.cfg["mini_bar"] = False
        config.save(self.cfg)
        self.btn_mini.setChecked(False)
        self.action_mini.setChecked(False)
        self.set_status("Mini bar closed")

    def _sync_mini_bar(self, force: bool = False) -> None:
        bar = self.mini_bar
        if bar is None or (not force and not bar.isVisible()):
            return
        track = self.current_track
        bar.set_radio_mode(self.radio_mode)
        if self.radio_mode:
            bar.set_track(self._radio_title(), self._radio_subtitle(), theme.GREEN)
            bar.set_playing(self._playing)
            self._radio_sync_wallpaper()
            return
        if self.online_track is not None:
            bar.set_track(self.online_track.title, self.online_track.artist, theme.GREEN)
            if self._mini_cover_pixmap is not None:
                bar.set_cover(self._mini_cover_pixmap)
            bar.set_playing(self._playing)
            return
        bar.set_track(track.display_title if track else "Nothing playing",
                      track.display_artist if track else "", self._tint)
        if self._mini_cover_pixmap is not None:
            bar.set_cover(self._mini_cover_pixmap)
        bar.set_playing(self._playing)

    # ------------------------------------------------------------------ radio #
    def toggle_radio(self) -> None:
        """Radio button: stop when playing, otherwise offer the station list."""
        if self.radio_mode:
            self._stop_radio(announce=True)
            return
        menu = self._build_radio_menu()
        menu.exec(self.btn_radio.mapToGlobal(QPoint(0, self.btn_radio.height())))
        menu.deleteLater()
        if not self.radio_mode:                  # the picker was dismissed
            self.btn_radio.setChecked(False)

    def _radio_context_menu(self, pos) -> None:
        """Right-click the radio button: switch stations without stopping first."""
        menu = self._build_radio_menu()
        menu.exec(self.btn_radio.mapToGlobal(pos))
        menu.deleteLater()

    # --------------------------------------------------------------- stations #
    def _build_radio_menu(self) -> QMenu:
        """The station picker shown under the radio button."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for category, stations in radio.stations_by_category():
            group = menu.addMenu(category)
            group.setToolTipsVisible(True)
            for station in stations:
                action = group.addAction(station.name)
                action.setCheckable(True)
                action.setChecked(self.radio_mode and self.radio_station == station.key)
                action.setToolTip(station.blurb)
                action.triggered.connect(
                    lambda _checked=False, key=station.key: self.play_station(key))
        if self.radio_mode:
            menu.addSeparator()
            stop = menu.addAction("Stop radio")
            stop.triggered.connect(lambda: self._stop_radio(announce=True))
        return menu

    def _radio_station_obj(self):
        return radio.station(self.radio_station)

    def _radio_title(self) -> str:
        """The song of a song-list station, else the station name."""
        if self.radio_song:
            return radio.song_title(self.radio_song)
        station = self._radio_station_obj()
        return station.name if station else "Radio"

    def _radio_subtitle(self) -> str:
        station = self._radio_station_obj()
        if station is None:
            return ""
        return station.name if station.has_songs else station.blurb

    def _radio_refresh_ui(self) -> None:
        """Paint every "now playing" surface for the current radio station."""
        title, subtitle = self._radio_title(), self._radio_subtitle()
        station = self._radio_station_obj()
        self._art_request = None
        self.artwork.set_art(None, theme.GREEN)
        self.artwork.set_playing(True)
        self.lbl_title.setText(title)
        self.lbl_artist.setText(subtitle)
        self.lbl_album.setText("")
        self.lbl_tech.setText(station.name if station else "Radio")
        self.lbl_total.setText("")
        self.bar_title.setText(title)
        self.bar_artist.setText(subtitle)
        self.bar_cover.set_art(None, theme.GREEN)
        self._radio_sync_wallpaper()
        self._push_presence()
        self._sync_mini_bar()

    def play_station(self, key: str) -> None:
        """Start one of the radio stations (picker or tray)."""
        station = radio.station(key)
        if station is None:
            return
        if self.radio_mode and self.radio_station == key:
            if not self._playing:
                self.toggle_play()
            self.btn_radio.setChecked(True)
            self._sync_radio_actions()
            return
        self._start_radio(key)

    def _start_radio(self, station_key: str | None = None) -> None:
        station = (radio.station(station_key or self.radio_station)
                   or radio.station(radio.DEFAULT_STATION))
        if station is None:
            return
        self._leave_online()
        if self.engine.is_playing():
            self.engine.stop()
        self.radio_mode = True
        self._end_handled = False
        self.radio_station = station.key
        if self.cfg.get("radio_station") != station.key:
            self.cfg["radio_station"] = station.key
            config.save(self.cfg)
        if station.has_songs:
            self.radio_queue = list(station.songs)
            random.shuffle(self.radio_queue)
            self.radio_song = self.radio_queue[0] if self.radio_queue else None
            url = radio.stream_url(self.radio_song) if self.radio_song else ""
        else:
            self.radio_queue = []
            self.radio_song = None
            url = station.stream
        if not url or not self.engine.play_path(url):
            self.radio_mode = False
            self.radio_song = None
            self._playing = False
            self._refresh_play_icon()
            self.playlist.set_playing(False)
            self.artwork.set_playing(False)
            self.btn_radio.setChecked(False)
            self._sync_radio_actions()
            self.set_status(f"Could not start {station.name} — check your connection"
                            if url else f"{station.name} has no stream right now")
            return
        self._playing = True
        self._refresh_play_icon()
        self.playlist.set_playing(False)
        self.btn_radio.setChecked(True)
        self._sync_radio_actions()
        if self.btn_play.isVisible():
            self.btn_play.set_accent(theme.GREEN)
        self._radio_refresh_ui()
        self.set_status(f"{station.name} — {self._radio_title()}"
                        if station.has_songs else f"Radio on — {station.name}")

    def _stop_radio(self, announce: bool = False) -> None:
        station = self._radio_station_obj()
        self.engine.stop()
        self.radio_mode = False
        self.radio_song = None
        self.radio_queue = []
        self._playing = False
        self._refresh_play_icon()
        self.playlist.set_playing(False)
        self.artwork.set_playing(False)
        self.btn_radio.setChecked(False)
        self._sync_radio_actions()
        if self.btn_play.isVisible():
            self.btn_play.set_accent(theme.CYAN)
        if self.mini_bar is not None:
            self.mini_bar.set_live_wallpaper(None)
        if self.current_track is not None:
            self._update_now_playing(self.current_track)
        else:
            self.lbl_title.setText("Nothing playing")
            self.lbl_artist.setText("—")
            self.lbl_album.setText("")
            self.lbl_tech.setText("")
            self.lbl_total.setText("0:00")
            self.bar_title.setText("Nothing playing")
            self.bar_artist.setText("—")
            self.artwork.set_art(None, theme.VIOLET)
        self._push_presence()
        self._sync_mini_bar()
        if announce:
            self.set_status(f"{station.name} stopped" if station else "Radio stopped")

    def _radio_next(self, step: int = 1) -> None:
        if not self.radio_mode:
            return
        station = self._radio_station_obj()
        if station is None:
            return
        if not station.has_songs:
            # a live stream has no "next song": the next button moves stations
            self._radio_cycle_station(step)
            return
        if not self.radio_queue:
            self.radio_queue = list(station.songs)
            random.shuffle(self.radio_queue)
        try:
            index = self.radio_queue.index(self.radio_song or "")
        except ValueError:
            index = -1
        index = (index + step) % len(self.radio_queue)
        self.radio_song = self.radio_queue[index]
        if self.engine.play_path(radio.stream_url(self.radio_song)):
            self._playing = True
            self._refresh_play_icon()
            self._radio_refresh_ui()
            self.set_status(f"{station.name} — {self._radio_title()}")
        else:
            self.set_status(f"The {station.name} stream was interrupted")

    def _radio_cycle_station(self, step: int = 1) -> None:
        keys = [station.key for station in radio.STATIONS]
        key = self.radio_station if self.radio_station in keys else radio.DEFAULT_STATION
        self._start_radio(keys[(keys.index(key) + step) % len(keys)])

    def _radio_sync_wallpaper(self) -> None:
        """Give the mini bar its station's locally generated live scene."""
        bar = self.mini_bar
        if bar is None:
            return
        station = self._radio_station_obj()
        if not self.radio_mode or station is None:
            bar.set_live_wallpaper(None)
            return
        bar.set_live_wallpaper(live_wallpaper.scene_for(station.category), station.key)

    def _sync_radio_mini(self) -> None:
        """Mini bar text while the radio plays (called from _sync_mini_bar)."""
        if self.radio_mode:
            bar = self.mini_bar
            if bar is not None:
                bar.set_track(self._radio_title(), self._radio_subtitle(), theme.GREEN)
                bar.set_playing(self._playing)
                self._radio_sync_wallpaper()

    def toggle_performance_mode(self) -> None:
        self._apply_performance_mode(not self.performance_mode, announce=True)

    def _apply_performance_mode(self, enabled: bool, announce: bool = False) -> None:
        """Minimal-work mode: no animations, slower timers, lighter mini bar."""
        self.performance_mode = bool(enabled)
        self.cfg["performance_mode"] = self.performance_mode
        config.save(self.cfg)

        if self.performance_mode:
            self._anim_timer.stop()
            self._tint_anim.stop()
            self._reveal_anim.stop()
            self._flash_anim.stop()
            self.artwork.set_reveal(1.0)
            self.now_block.setGraphicsEffect(None)       # no offscreen render at all
            self._now_opacity = None                     # Qt deleted the old effect
            self.playlist.set_flash(0.0)
            self.meter.set_active(False)
            self._tick_timer.setInterval(1000)
        else:
            self._tick_timer.setInterval(250)
            self._anim_timer.start(90)
            self.artwork.set_reveal(1.0)
            if self.now_block.graphicsEffect() is None:
                self._attach_now_effect()
            elif self._now_opacity is not None:
                self._now_opacity.setOpacity(1.0)

        if self.mini_bar is not None:
            self.mini_bar.set_performance(self.performance_mode)
        self.presence.set_performance(self.performance_mode)

        self.btn_perf.setChecked(self.performance_mode)
        self.action_perf.setChecked(self.performance_mode)
        self.btn_perf.set_variant("solid" if self.performance_mode else "ghost")
        self.btn_perf.set_accent(theme.GREEN if self.performance_mode else theme.TEXT_MUTED)
        if announce:
            self.set_status("Performance mode ON — animations off, one refresh per second"
                            if self.performance_mode
                            else "Performance mode off — animations back on")

    # ----------------------------------------------------------- animations #
    def _attach_now_effect(self) -> None:
        """(Re)create the fade effect - Qt deletes an effect once it is detached."""
        effect = QGraphicsOpacityEffect(self.now_block)
        effect.setOpacity(1.0)
        self._now_opacity = effect
        self.now_block.setGraphicsEffect(effect)

    def _apply_tint(self, color) -> None:
        self._tint = theme.color(color)
        self.artwork.set_accent(self._tint)
        self.playlist.set_tint(self._tint)
        # the middle of the green top bar follows the song like Spotify's
        if getattr(self, "topbar", None) is not None:
            self.topbar.set_accent(self._tint)
        # the play bar takes the same faint tint along its top edge
        if getattr(self, "player_bar", None) is not None:
            self.player_bar.set_accent(self._tint)
        # ...and the mini bar follows the song's colour frame by frame —
        # otherwise it would stay on the previous song's tint forever
        if self.mini_bar is not None:
            self.mini_bar.set_accent(self._tint)
        self._refresh_play_icon()

    def _on_tint_step(self, value) -> None:
        self._apply_tint(value)

    def _on_reveal_step(self, value) -> None:
        self.artwork.set_reveal(value)
        if self._now_opacity is not None:
            self._now_opacity.setOpacity(0.25 + 0.75 * float(value))

    def _on_flash_step(self, value) -> None:
        self.playlist.set_flash(value)

    def _animate_song_change(self, accent: str) -> None:
        """Fade the new song in and morph the accent colour to the cover's hue."""
        target = theme.color(accent)
        if self.performance_mode:
            self._apply_tint(target)
            self.artwork.set_reveal(1.0)
            if self._now_opacity is not None:
                self._now_opacity.setOpacity(1.0)
            return
        self._tint_anim.stop()
        self._tint_anim.setStartValue(QColor(self._tint))
        self._tint_anim.setEndValue(QColor(target))
        self._tint_anim.start()
        self._reveal_anim.stop()
        self._reveal_anim.setStartValue(0.0)
        self._reveal_anim.setEndValue(1.0)
        self._reveal_anim.start()

    def _flash_current_row(self) -> None:
        if self.performance_mode:
            return
        self._flash_anim.stop()
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.start()

    # ===================================================================== #
    #  timers
    # ===================================================================== #
    def _tick(self) -> None:
        if self._closing:
            return
        self._poll_instance_mailbox()
        self._drain_ui_queue()
        # libVLC may report the previous media's end just after a new song
        # starts; that must not be mistaken for the new song finishing
        fresh_media = self.engine.recently_started()
        for kind, _payload in self.engine.poll():
            if kind == "ended":
                if fresh_media:
                    continue
                self._handle_ended()
            elif kind == "error":
                self._error_streak += 1
                if self._error_streak > max(3, len(self.queue)):
                    self.stop()
                    self.set_status("These files could not be played — check the VLC installation")
                else:
                    self.set_status("Playback error — skipping to the next song")
                    self.next_track(1)

        state = self.engine.state()
        if state.endswith("Playing"):
            self._end_handled = False
            self._error_streak = 0
        elif state.endswith("Ended") and self._playing and not fresh_media:
            self._handle_ended()

        playing = self.engine.is_playing()
        if playing != self._playing:
            self._playing = playing
            self._refresh_play_icon()
            self.playlist.set_playing(playing)
            self.artwork.set_playing(playing)
            self._push_presence()
            self._sync_mini_bar()

        marker = f"{self.presence.enabled}|{self.presence.status_text()}|"
        if marker != getattr(self, "_discord_marker", ""):
            self._discord_marker = marker

        # keep the tray tooltip / menu in step with the song and play state
        if self.radio_mode:
            tray_title = self._radio_title()
        elif self.online_track is not None:
            tray_title = self.online_track.title
        else:
            tray_title = self.current_track.display_title if self.current_track else ""
        tray_key = f"{tray_title}|{self._playing}"
        if tray_key != getattr(self, "_tray_key", "") and self.tray_available:
            self._tray_key = tray_key
            self.tray.setToolTip(f"Spoti Player — {tray_title}" if tray_title else "Spoti Player")
            self.action_play.setText("Pause" if self._playing else "Play")
            self.action_show.setText("Hide window" if self.isVisible() else "Show window")

        if self.online_track is not None:
            length = int(online.PREVIEW_SECONDS * 1000)
            if not self._seeking:
                position = min(length, self.engine.time_ms())
                self.seek.set_position(position / length)
                self.lbl_now.setText(format_time(position / 1000.0))
                if self.mini_bar is not None and self.mini_bar.isVisible():
                    self.mini_bar.set_progress(position / length)
        elif self.current_track is not None:
            length = self.engine.length_ms() or int((self.current_track.duration or 0) * 1000)
            if length and not self.current_track.duration:
                self.current_track.duration = length / 1000.0
                self.lbl_total.setText(format_time(self.current_track.duration))
            if not self._seeking:
                position = self.engine.time_ms()
                if length > 0:
                    self.seek.set_position(position / length)
                    self.lbl_now.setText(format_time(position / 1000.0))
                    if self.mini_bar is not None and self.mini_bar.isVisible():
                        self.mini_bar.set_progress(position / length)

    def _drain_ui_queue(self) -> None:
        touched_meta = False
        meta_finished = False
        processed = 0
        while processed < 400:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if kind == "scan_done":
                token, tracks = payload
                self._apply_scan(token, tracks)
            elif kind == "meta":
                touched_meta = True
                track = payload
                if self.current_track and track.path == self.current_track.path:
                    self._update_now_playing(track)
            elif kind == "meta_done":
                touched_meta = True
                meta_finished = True
            elif kind == "art":
                path, image, accent = payload
                if path == self._art_request:
                    self._apply_art(image, accent)
            elif kind == "online":
                token, query, results = payload
                self._show_online(token, query, results)
            elif kind == "online_error":
                token, query = payload
                if token == self._online_token and query == self.search.text().strip():
                    self.online_list.set_message(
                        "Could not reach the online catalogue — check your connection")
            elif kind == "online_art":
                request, image, accent = payload
                if request == self._online_art_request:
                    self._apply_art(image, accent)
            elif kind == "saving":
                track_id, fraction = payload
                if track_id in self._saving:
                    self._saving[track_id] = fraction
                    self.online_list.set_saving(track_id, fraction)
                    self.set_status(f"Downloading the full song… {int(fraction * 100)}%")
            elif kind == "saved_done":
                track_id, path = payload
                self._saving.pop(track_id, None)
                self.online_list.clear_saving(track_id)
                self.saved = saved.tracks()
                self._saved_ids = saved.ids()
                self.online_list.set_saved(self._saved_ids)
                self._sync_saved_row()
                if self._saved_only:
                    self.rebuild_queue(keep_current=True)
                self.set_status(
                    f"Downloaded — {os.path.basename(path)}"
                    if os.path.isfile(path) else "That song could not be downloaded")
            elif kind == "saved_error":
                track_id, message = payload
                self._saving.pop(track_id, None)
                self.online_list.clear_saving(track_id)
                self.set_status(f"Could not download that song: {message}")
            elif kind == "error":
                self.set_status(str(payload))
        if touched_meta:
            self.playlist.viewport().update()
            self._update_counts()
        if meta_finished and self.sort_mode in self.TAG_SORTS and not self._manual_order:
            self.rebuild_queue(keep_current=True)

    TAG_SORTS = {"Title (A→Z)", "Artist (A→Z)", "Album (A→Z)",
                 "Duration (short→long)", "Duration (long→short)"}

    def _animate(self) -> None:
        if self._closing:
            return
        self._phase += 1
        self.meter.set_active(self._playing)
        self.meter.tick()
        self.artwork.tick(self._phase * 0.14)
        if self._playing:
            self.playlist.tick()
            if self.online_track is not None:
                self.online_list.tick()

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        self._status_timer.start(6000)

    # ===================================================================== #
    #  lifecycle
    # ===================================================================== #
    def _build_tray(self) -> None:
        """System tray icon: keeps the music alive after the window is closed."""
        self._really_quit = False
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Spoti Player")

        menu = QMenu(self)
        self.action_show = QAction("Show / hide window", self)
        self.action_show.triggered.connect(self.toggle_window)
        self.action_play = QAction("Play / pause", self)
        self.action_play.triggered.connect(self.toggle_play)
        self.action_next = QAction("Next song", self)
        self.action_next.triggered.connect(lambda: self.next_track(1))
        self.action_prev = QAction("Previous song", self)
        self.action_prev.triggered.connect(lambda: self.next_track(-1))
        self.action_tray = QAction("Keep playing in the tray when closed", self)
        self.action_tray.setCheckable(True)
        self.action_tray.setChecked(bool(self.cfg.get("minimize_to_tray", True)))
        self.action_tray.toggled.connect(self._on_tray_setting_changed)
        self.action_mini = QAction("Mini bar on top", self)
        self.action_mini.setCheckable(True)
        self.action_mini.setChecked(bool(self.cfg.get("mini_bar", False)))
        self.action_mini.toggled.connect(self._on_mini_action_toggled)
        # radio: the same stations as the button picker, grouped by category
        self.radio_menu = QMenu("Radio", menu)
        self.radio_menu.setToolTipsVisible(True)
        self._station_actions: dict[str, QAction] = {}
        for category, stations in radio.stations_by_category():
            group = self.radio_menu.addMenu(category)
            group.setToolTipsVisible(True)
            for station in stations:
                action = group.addAction(station.name)
                action.setCheckable(True)
                action.setToolTip(station.blurb)
                action.triggered.connect(
                    lambda _checked=False, key=station.key: self.play_station(key))
                self._station_actions[station.key] = action
        self.radio_menu.addSeparator()
        self.action_radio_stop = self.radio_menu.addAction("Stop radio")
        self.action_radio_stop.triggered.connect(lambda: self._stop_radio(announce=True))
        self.action_radio = self.radio_menu.menuAction()
        self.action_perf = QAction("Performance mode (minimal usage)", self)
        self.action_perf.setCheckable(True)
        self.action_perf.setChecked(self.performance_mode)
        self.action_perf.toggled.connect(self._on_perf_action_toggled)
        self.action_settings = QAction("Settings", self)
        self.action_settings.triggered.connect(self.open_settings)
        self.action_quit = QAction("Quit Spoti Player", self)
        self.action_quit.triggered.connect(self.quit_app)

        menu.addAction(self.action_show)
        menu.addSeparator()
        menu.addAction(self.action_play)
        menu.addAction(self.action_prev)
        menu.addAction(self.action_next)
        menu.addSeparator()
        menu.addAction(self.action_mini)
        menu.addAction(self.action_radio)
        menu.addAction(self.action_perf)
        menu.addAction(self.action_tray)
        menu.addSeparator()
        menu.addAction(self.action_settings)
        menu.addAction(self.action_quit)
        self.tray.setContextMenu(menu)
        self._sync_radio_actions()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self.restore_window)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window()

    def _set_radio(self, on: bool) -> None:
        if on and not self.radio_mode:
            self._start_radio()
        elif not on and self.radio_mode:
            self._stop_radio(announce=True)

    def _sync_radio_actions(self) -> None:
        """Mirror the current station on the button picker and the tray menu."""
        for key, action in getattr(self, "_station_actions", {}).items():
            action.setChecked(self.radio_mode and key == self.radio_station)
        stop = getattr(self, "action_radio_stop", None)
        if stop is not None:
            stop.setEnabled(self.radio_mode)

    def _on_tray_setting_changed(self, checked: bool) -> None:
        self.cfg["minimize_to_tray"] = bool(checked)
        config.save(self.cfg)
        self.set_status("Closing the window will keep playing in the tray"
                        if checked else "Closing the window will quit Spoti Player")

    def _on_mini_action_toggled(self, checked: bool) -> None:
        if checked and (self.mini_bar is None or not self.mini_bar.isVisible()):
            self.show_mini_bar()
        elif not checked and self.mini_bar is not None and self.mini_bar.isVisible():
            self.hide_mini_bar()

    def _on_perf_action_toggled(self, checked: bool) -> None:
        if bool(checked) != self.performance_mode:
            self._apply_performance_mode(bool(checked), announce=True)

    @property
    def tray_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable() and getattr(self, "tray", None) is not None

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.restore_window()

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._really_quit = True
        self.close()

    def should_minimize_to_tray(self) -> bool:
        """True when closing should keep the player alive in the tray.

        Only while a song is *actually playing* - if playback is stopped or
        paused the ✕ really closes the app, and holding Shift always closes.
        """
        if self._really_quit or not self.cfg.get("minimize_to_tray", True):
            return False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            return False
        return bool(self._playing)

    def _notify_tray(self) -> None:
        title = self.current_track.display_title if self.current_track else "Spoti Player"
        artist = self.current_track.display_artist if self.current_track else ""
        try:
            self.tray.showMessage(
                "Still playing in the tray",
                f"{title}\n{artist}\n\nRight-click the tray icon → Quit Spoti Player to exit."
                if artist else f"{title}\n\nRight-click the tray icon → Quit to exit.",
                QSystemTrayIcon.Information, 5000)
        except Exception:
            pass

    def closeEvent(self, event):
        if self.should_minimize_to_tray():
            event.ignore()
            self.hide()
            self._notify_tray()
            self.set_status("Minimised to the tray — right-click the tray icon → Quit to exit")
            return
        self._shutdown()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            rect = self.geometry()          # client rect: setGeometry() round-trips exactly
            self.cfg["geometry"] = (f"{rect.width()}x{rect.height()}+"
                                    f"{rect.x()}+{rect.y()}")
            self.cfg["volume"] = self.engine.volume
            self.cfg["muted"] = self.engine.muted
            self.cfg["device_id"] = self.engine.device_id
            self.cfg["device_name"] = self.engine.device_name
            self.cfg["liked"] = sorted(self.liked)
            self.cfg["shuffle"] = self.shuffle
            self.cfg["repeat"] = self.repeat
            self.cfg["sort_mode"] = self.sort_mode
            self.cfg["discord_enabled"] = self.presence.enabled
            self.cfg["discord_client_id"] = self.presence.client_id
            self.cfg["discord_large_image"] = self.presence.large_image
            config.save(self.cfg)
        finally:
            self._tick_timer.stop()
            self._anim_timer.stop()
            try:
                if self.mini_bar is not None:
                    self.mini_bar.hide()
                    self.mini_bar.close()
            except Exception:
                pass
            try:
                if getattr(self, "tray", None) is not None:
                    self.tray.hide()
            except Exception:
                pass
            try:
                self.presence.stop()
            except Exception:
                pass
            try:
                self.engine.shutdown()
            except Exception:
                pass


#: the window used to be called "Potato Player"; keep the old name working so
#: existing scripts (and the test-suite) keep importing the same class.
PotatoPlayerWindow = SpotiPlayerWindow
