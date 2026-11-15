"""Spoti Player — entry point (Qt 6 build).

Run with:  python main.py            (or double-click SpotiPlayer.bat)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from potatoplayer import ENGINE_ERROR_HINT, __version__        # noqa: E402
from potatoplayer import single_instance, theme                # noqa: E402
from potatoplayer.engine import EngineError                    # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="spoti-player",
        description="Spoti Player — music player with output-drive control, device switching, "
                    "a 10-band equalizer and Discord Rich Presence.",
    )
    parser.add_argument("-f", "--folder", help="music folder to load on start")
    parser.add_argument("paths", nargs="*",
                        help="a music folder, or audio file(s) to open — files start playing "
                             "right away (this is what Explorer sends on double-click)")
    parser.add_argument("--startup", action="store_true",
                        help="start minimised in the tray (used by the Run-at-startup entry)")
    parser.add_argument("-d", "--vlc-dir", dest="vlc_dir",
                        help=r"folder containing libvlc.dll (default: auto-detect, e.g. C:\Program Files\VideoLAN\VLC)")
    parser.add_argument("--devices", action="store_true",
                        help="print the detected audio output devices and exit")
    parser.add_argument("--mini", action="store_true",
                        help="start with the always-on-top mini bar visible (nice with games)")
    parser.add_argument("--discord-id", dest="discord_id",
                        help="save a Discord Application ID for Rich Presence and exit")
    parser.add_argument("-v", "--version", action="version", version=f"Spoti Player {__version__}")
    return parser.parse_args(argv)


def print_devices(vlc_dir=None) -> int:
    """Console diagnostic: show libVLC + the available output devices."""
    from potatoplayer.engine import VlcEngine
    try:
        engine = VlcEngine(vlc_dir)
    except EngineError as exc:
        print(f"{exc}\n\n{ENGINE_ERROR_HINT}", file=sys.stderr)
        return 2
    print(f"libVLC {engine.version()}  ({engine.vlc_dir})")
    print("Equalizer bands:", ", ".join(f"{f} Hz" for f in engine.EQ_FREQS))
    print("Presets:", ", ".join(engine.PRESET_NAMES))
    print("Audio output devices:")
    for name, device_id in engine.output_devices(max_age=0.0):
        print(f"  - {name}   [{device_id}]")
    engine.shutdown()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.devices:
        return print_devices(args.vlc_dir)

    if args.discord_id is not None:
        from potatoplayer import config
        cfg = config.load()
        cfg["discord_client_id"] = args.discord_id.strip()
        config.save(cfg)
        print(f"Discord Application ID saved: {args.discord_id.strip() or '(cleared)'}")
        print(f"Config file: {config.config_path()}")
        return 0

    # ---- what to open ------------------------------------------------- #
    initial_folder = args.folder
    initial_files: list[str] = []
    for raw in args.paths or []:
        path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(path):
            initial_files.append(path)
        elif os.path.isdir(path) and not initial_folder:
            initial_folder = path
    if initial_files and not initial_folder:
        initial_folder = os.path.dirname(initial_files[0])
    requested = list(args.paths or []) or list(initial_files)

    # ---- exactly one running copy ------------------------------------- #
    # A double-clicked song must not open a second player: hand the paths to
    # the copy that is already running and get out of the way.
    if not single_instance.acquire():
        single_instance.send(requested, show=not requested)
        return 0

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Spoti Player")
    app.setStyle("Fusion")
    # the window may be hidden to the tray while the music keeps playing
    app.setQuitOnLastWindowClosed(False)
    try:
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass
    app.setStyleSheet(theme.stylesheet())

    from potatoplayer.app import SpotiPlayerWindow
    try:
        window = SpotiPlayerWindow(initial_folder=initial_folder,
                                   vlc_dir=args.vlc_dir,
                                   initial_files=initial_files)
    except EngineError as exc:
        QMessageBox.critical(None, "Spoti Player — missing component",
                             f"{exc}\n\n{ENGINE_ERROR_HINT}")
        print(f"{exc}\n{ENGINE_ERROR_HINT}", file=sys.stderr)
        return 2

    if args.startup and window.tray_available:
        # started with Windows: stay out of the way, only the tray icon shows
        window.hide()
        QTimer.singleShot(900, lambda: window.tray.showMessage(
            "Spoti Player", "Started with Windows — running in the tray",
            QSystemTrayIcon.Information, 4000))
    else:
        window.show()
        window.raise_()
        window.activateWindow()
    if args.mini:
        window.show_mini_bar()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
